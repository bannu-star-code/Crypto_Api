import json
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote_plus
from urllib.request import Request, urlopen

import certifi
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings


PORTFOLIO_PATH = "/trade/api/v2/user/portfolio"


class CoinSwitchAPIError(Exception):
    pass


class CoinSwitchClient:
    def _headers(self):
        if not settings.COINSWITCH_API_KEY:
            raise CoinSwitchAPIError("Set COINSWITCH_API_KEY in your .env file.")
        if not settings.COINSWITCH_API_SECRET:
            raise CoinSwitchAPIError("Set COINSWITCH_API_SECRET in your .env file.")

        try:
            secret_key = bytes.fromhex(settings.COINSWITCH_API_SECRET)
            private_key = Ed25519PrivateKey.from_private_bytes(secret_key)
        except (ValueError, TypeError) as exc:
            raise CoinSwitchAPIError(
                "COINSWITCH_API_SECRET must be a 64-character hexadecimal "
                "Ed25519 private key."
            ) from exc

        epoch = str(int(time.time() * 1000))
        signature_message = (
            f"GET{unquote_plus(PORTFOLIO_PATH)}{epoch}".encode("utf-8")
        )
        signature = private_key.sign(signature_message).hex()
        return {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": settings.COINSWITCH_API_KEY,
            "X-AUTH-SIGNATURE": signature,
            "X-AUTH-EPOCH": epoch,
            "Accept": "application/json",
        }

    def get_portfolio(self):
        request = Request(
            f"{settings.COINSWITCH_API_BASE_URL}{PORTFOLIO_PATH}",
            headers=self._headers(),
            method="GET",
        )
        tls_context = ssl.create_default_context(cafile=certifi.where())
        try:
            with urlopen(request, timeout=20, context=tls_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 403:
                raise CoinSwitchAPIError(
                    "CoinSwitch denied access (HTTP 403). Check that your API key "
                    "has portfolio access and that the endpoint and signing method "
                    "match the API documentation for your account."
                ) from exc
            raise CoinSwitchAPIError(
                f"CoinSwitch returned HTTP {exc.code}."
            ) from exc
        except URLError as exc:
            raise CoinSwitchAPIError(
                f"Could not connect to CoinSwitch: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise CoinSwitchAPIError(
                "CoinSwitch returned a response that was not valid JSON."
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise CoinSwitchAPIError(
                "CoinSwitch returned an unexpected portfolio response."
            )
        return payload["data"]
