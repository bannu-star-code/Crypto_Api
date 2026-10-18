import hashlib
import hmac
import json
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi
from django.conf import settings


WALLET_BALANCES_PATH = "/v2/wallet/balances"
POSITIONS_PATH = "/v2/positions/margined"
SPOT_PRODUCTS_PATH = "/v2/products"
ORDERS_PATH = "/v2/orders"


class DeltaExchangeAPIError(Exception):
    pass


class DeltaExchangeClient:
    @staticmethod
    def _error_detail(payload):
        if not isinstance(payload, dict):
            return None

        detail = payload.get("message")
        api_error = payload.get("error")
        if isinstance(api_error, dict):
            code = api_error.get("code")
            detail = api_error.get("message") or detail or code
            context = api_error.get("context")
            if isinstance(context, dict):
                context_parts = []
                for key, label in (
                    ("additional_margin_required", "additional margin required"),
                    ("available_balance", "available balance"),
                    ("required_margin", "required margin"),
                ):
                    value = context.get(key)
                    if isinstance(value, (str, int, float)):
                        context_parts.append(f"{label}: {value}")
                if context_parts:
                    detail = f"{detail or code}: {', '.join(context_parts)}"
            if code == "insufficient_margin":
                detail = (
                    f"{detail or code}. Delta rejected the order because it "
                    "does not see enough available funds for it. Check the "
                    "available INR balance (not total balance); funds reserved "
                    "by open orders or positions may not be available."
                )
        elif isinstance(api_error, str):
            detail = api_error

        if not isinstance(detail, str):
            return None
        detail = re.sub(r"\s+", " ", detail).strip()
        return detail[:500] or None

    @classmethod
    def _http_error_detail(cls, error):
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return cls._error_detail(payload)

    def _request_json(self, method, path, query_params=None, body=None):
        api_key = settings.DELTA_API_KEY
        api_secret = settings.DELTA_API_SECRET
        if not api_key or not api_secret:
            raise DeltaExchangeAPIError(
                "Set DELTA_API_KEY and DELTA_API_SECRET in your .env file."
            )

        timestamp = str(int(time.time()))
        query_string = f"?{urlencode(query_params)}" if query_params else ""
        body_string = json.dumps(body, separators=(",", ":")) if body is not None else ""
        signature_message = method + timestamp + path + query_string + body_string
        signature = hmac.new(
            api_secret.encode("utf-8"),
            signature_message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        request = Request(
            f"{settings.DELTA_API_BASE_URL}{path}{query_string}",
            data=body_string.encode("utf-8") if body is not None else None,
            headers={
                "api-key": api_key,
                "timestamp": timestamp,
                "signature": signature,
                "Accept": "application/json",
                "User-Agent": "Django-Delta-Portfolio",
                **(
                    {"Content-Type": "application/json"}
                    if body is not None
                    else {}
                ),
            },
            method=method,
        )
        tls_context = ssl.create_default_context(cafile=certifi.where())
        try:
            with urlopen(request, timeout=20, context=tls_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = self._http_error_detail(exc)
            if exc.code in (401, 403):
                raise DeltaExchangeAPIError(
                    f"Delta Exchange returned HTTP {exc.code}"
                    f"{f': {detail}' if detail else '.'} Check the Delta API "
                    "key/secret, Read Data and Trading permissions, and the API "
                    "key's IP whitelist."
                ) from exc
            if method == "POST" and exc.code >= 500:
                raise DeltaExchangeAPIError(
                    f"Delta returned HTTP {exc.code}; it may have accepted the "
                    "order before the error. Check Delta order history before "
                    "trying again."
                ) from exc
            raise DeltaExchangeAPIError(
                f"Delta Exchange returned HTTP {exc.code}"
                f"{f': {detail}' if detail else '.'}"
            ) from exc
        except URLError as exc:
            if method == "POST":
                raise DeltaExchangeAPIError(
                    "Delta did not confirm whether it accepted this order. "
                    "Check Delta order history before trying again."
                ) from exc
            raise DeltaExchangeAPIError(
                f"Could not connect to Delta Exchange: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            if method == "POST":
                raise DeltaExchangeAPIError(
                    "Delta returned an unreadable response, so the order outcome "
                    "is unknown. Check Delta order history before trying again."
                ) from exc
            raise DeltaExchangeAPIError(
                "Delta Exchange returned a response that was not valid JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise DeltaExchangeAPIError(
                "Delta Exchange returned an unexpected response."
            )
        if payload.get("success") is False:
            details = self._error_detail(payload) or "request rejected"
            raise DeltaExchangeAPIError(f"Delta Exchange API error: {details}.")

        result = payload.get("result")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        raise DeltaExchangeAPIError(
            "Delta Exchange returned an unexpected account-data format."
        )

    def _get_json(self, path, query_params=None):
        return self._request_json("GET", path, query_params=query_params)

    def get_wallet_balances(self):
        return self._get_json(WALLET_BALANCES_PATH)

    def get_positions(self):
        return self._get_json(POSITIONS_PATH)

    def get_spot_products(self):
        return self._get_json(
            SPOT_PRODUCTS_PATH,
            {"contract_types": "spot", "page_size": 100},
        )

    def get_spot_ticker(self, symbol):
        return self._get_json(f"/v2/tickers/{symbol}")[0]

    def place_order(self, order):
        result = self._request_json("POST", ORDERS_PATH, body=order)
        if len(result) != 1 or not isinstance(result[0], dict):
            raise DeltaExchangeAPIError(
                "Delta returned an unexpected order response. Check Delta order "
                "history before trying again."
            )
        return result[0]

    def get_account_data(self):
        return {
            "balances": self.get_wallet_balances(),
            "positions": self.get_positions(),
        }
