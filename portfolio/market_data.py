import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi


API_BASE_URL = "https://api.coingecko.com/api/v3"
MAX_SEARCH_RESULTS = 10


class MarketDataError(Exception):
    pass


class MarketDataClient:
    def _get_json(self, url):
        request = Request(url, headers={"Accept": "application/json"})
        tls_context = ssl.create_default_context(cafile=certifi.where())

        try:
            with urlopen(request, timeout=15, context=tls_context) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                raise MarketDataError(
                    "The price service rate limit was reached. Wait a little and try again."
                ) from exc
            raise MarketDataError(
                f"The price service returned HTTP {exc.code}."
            ) from exc
        except URLError as exc:
            raise MarketDataError(
                f"Could not connect to the price service: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise MarketDataError(
                "The price service returned a response that was not valid JSON."
            ) from exc

    def search_coins_inr(self, query):
        query = query.strip()
        if not query:
            raise MarketDataError("Enter a coin name or symbol to search.")
        if len(query) > 100:
            raise MarketDataError("Search text must be 100 characters or fewer.")

        search_url = f"{API_BASE_URL}/search?{urlencode({'query': query})}"
        search_payload = self._get_json(search_url)
        if not isinstance(search_payload, dict) or not isinstance(
            search_payload.get("coins"), list
        ):
            raise MarketDataError(
                "The price service returned an unexpected search response."
            )

        matches = [
            coin
            for coin in search_payload["coins"]
            if isinstance(coin, dict)
            and isinstance(coin.get("id"), str)
            and isinstance(coin.get("name"), str)
            and isinstance(coin.get("symbol"), str)
        ][:MAX_SEARCH_RESULTS]
        if not matches:
            return []

        ids = ",".join(coin["id"] for coin in matches)
        price_url = f"{API_BASE_URL}/simple/price?{urlencode({'ids': ids, 'vs_currencies': 'inr'})}"
        price_payload = self._get_json(price_url)
        if not isinstance(price_payload, dict):
            raise MarketDataError(
                "The price service returned an unexpected prices response."
            )

        results = []
        for coin in matches:
            price_data = price_payload.get(coin["id"])
            inr_price = price_data.get("inr") if isinstance(price_data, dict) else None
            try:
                price = float(inr_price)
            except (TypeError, ValueError):
                price = None

            results.append(
                {
                    "id": coin["id"],
                    "name": coin["name"],
                    "symbol": coin["symbol"].upper(),
                    "market_cap_rank": coin.get("market_cap_rank"),
                    "price_inr": price if price and price > 0 else None,
                }
            )

        return results
