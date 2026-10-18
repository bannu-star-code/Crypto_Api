import hashlib
import hmac
import io
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from urllib.error import HTTPError

from .delta_exchange import DeltaExchangeAPIError, DeltaExchangeClient


SPOT_PRODUCT = {
    "id": 117569,
    "symbol": "BTC_INR",
    "contract_type": "spot",
    "contract_unit_currency": "BTC",
    "state": "live",
    "trading_status": "operational",
    "tick_size": "0.1",
    "product_specs": {
        "underlying_precision": 6,
        "min_order_size": 0.000001,
        "max_order_size": 0.25,
        "only_reduce_only_orders_allowed": False,
    },
}


class AuthenticationAndPortfolioTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="test-user",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_coin_search_page_is_public(self):
        response = Client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search coin prices")

    def test_login_page_accepts_valid_credentials(self):
        client = Client()
        response = client.post(
            "/login/",
            {"username": "test-user", "password": "test-password"},
        )

        self.assertRedirects(response, "/")
        self.assertEqual(client.get("/portfolio/").status_code, 200)

    def test_login_page_rejects_invalid_credentials(self):
        response = Client().post(
            "/login/",
            {"username": "test-user", "password": "incorrect"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "username or password is incorrect")

    def test_signup_creates_account_and_signs_user_in(self):
        client = Client()
        response = client.post(
            "/signup/",
            {
                "username": "new-user",
                "password1": "Strong!TestPassword849",
                "password2": "Strong!TestPassword849",
            },
        )

        self.assertRedirects(response, "/portfolio/")
        self.assertTrue(
            get_user_model().objects.filter(username="new-user").exists()
        )
        self.assertEqual(client.get("/portfolio/").status_code, 200)

    def test_signup_rejects_duplicate_username(self):
        response = Client().post(
            "/signup/",
            {
                "username": "test-user",
                "password1": "Strong!TestPassword849",
                "password2": "Strong!TestPassword849",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_signup_rejects_weak_password(self):
        response = Client().post(
            "/signup/",
            {
                "username": "another-user",
                "password1": "password",
                "password2": "password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "too common")
        self.assertFalse(
            get_user_model().objects.filter(username="another-user").exists()
        )

    @override_settings(ALLOW_SIGNUPS=False)
    def test_signup_is_disabled_when_configured(self):
        response = Client().get("/signup/")

        self.assertEqual(response.status_code, 404)

    @override_settings(ALLOW_SIGNUPS=False)
    def test_login_hides_signup_link_when_signup_is_disabled(self):
        response = Client().get("/login/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Don't have an account?")

    def test_logout_requires_post_and_clears_authentication(self):
        response = self.client.post("/logout/")

        self.assertRedirects(response, "/")
        self.assertRedirects(self.client.get("/portfolio/"), "/login/?next=/portfolio/")

    def test_logout_does_not_accept_get(self):
        response = self.client.get("/logout/")

        self.assertEqual(response.status_code, 405)

    def test_portfolio_requires_authentication(self):
        response = Client().get("/portfolio/")

        self.assertRedirects(response, "/login/?next=/portfolio/")

    def test_spot_trade_endpoints_require_authentication(self):
        with (
            patch.object(DeltaExchangeClient, "get_spot_products") as get_products,
            patch.object(DeltaExchangeClient, "place_order") as place_order,
        ):
            preview_response = Client().post("/portfolio/spot/preview/", {})
            confirm_response = Client().post("/portfolio/spot/confirm/", {})

        self.assertRedirects(
            preview_response, "/login/?next=/portfolio/spot/preview/"
        )
        self.assertRedirects(
            confirm_response, "/login/?next=/portfolio/spot/confirm/"
        )
        get_products.assert_not_called()
        place_order.assert_not_called()

    def test_portfolio_is_available_after_login(self):
        with (
            patch.object(DeltaExchangeClient, "get_spot_products", return_value=[]),
            patch.object(DeltaExchangeClient, "get_positions", return_value=[]),
            patch.object(DeltaExchangeClient, "get_wallet_balances", return_value=[]),
        ):
            response = self.client.get("/portfolio/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wallet balances")
        self.assertContains(response, "Open positions")

    def test_wallet_balances_render_when_positions_request_fails(self):
        balances = [
            {
                "asset_symbol": "USD",
                "available_balance": "1",
                "balance": "1",
            }
        ] + [
            {
                "asset_symbol": f"ASSET{i}",
                "available_balance": "0",
                "balance": "0",
            }
            for i in range(6)
        ]
        with (
            patch.object(DeltaExchangeClient, "get_spot_products", return_value=[]),
            patch.object(DeltaExchangeClient, "get_wallet_balances", return_value=balances),
            patch.object(
                DeltaExchangeClient,
                "get_positions",
                side_effect=DeltaExchangeAPIError("Positions are unavailable."),
            ),
        ):
            response = self.client.get("/portfolio/")

        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "USD")
        self.assertContains(response, "Positions are unavailable.")
        self.assertEqual(html.count("<tr>"), 8)

    @patch.object(DeltaExchangeClient, "place_order")
    @patch.object(DeltaExchangeClient, "get_spot_ticker", return_value={"spot_price": "50000"})
    @patch.object(DeltaExchangeClient, "get_spot_products", return_value=[SPOT_PRODUCT])
    def test_inr_market_order_preview_is_not_submitted(
        self, get_products, get_ticker, place_order
    ):
        response = self.client.post(
            "/portfolio/spot/preview/",
            {
                "side": "buy",
                "product_id": str(SPOT_PRODUCT["id"]),
                "amount_mode": "inr",
                "amount": "5000",
                "order_type": "market_order",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm and place order")
        self.assertContains(response, "0.100000 BTC")
        get_products.assert_called_once_with()
        get_ticker.assert_called_once_with("BTC_INR")
        place_order.assert_not_called()

    @patch.object(DeltaExchangeClient, "place_order")
    @patch.object(DeltaExchangeClient, "get_spot_ticker")
    @patch.object(DeltaExchangeClient, "get_spot_products", return_value=[SPOT_PRODUCT])
    def test_inr_limit_sell_uses_limit_price_for_coin_size(
        self, get_products, get_ticker, place_order
    ):
        response = self.client.post(
            "/portfolio/spot/preview/",
            {
                "side": "sell",
                "product_id": str(SPOT_PRODUCT["id"]),
                "amount_mode": "inr",
                "amount": "5000",
                "order_type": "limit_order",
                "limit_price": "50000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sell")
        self.assertContains(response, "0.100000 BTC")
        self.assertContains(response, "₹50000")
        get_products.assert_called_once_with()
        get_ticker.assert_not_called()
        place_order.assert_not_called()

    @patch.object(DeltaExchangeClient, "place_order", return_value={"id": 987, "state": "open"})
    @patch.object(DeltaExchangeClient, "get_spot_products", return_value=[SPOT_PRODUCT])
    def test_confirmed_spot_order_is_submitted_once(self, get_products, place_order):
        token = signing.dumps(
            {
                "nonce": uuid.uuid4().hex,
                "symbol": "BTC_INR",
                "coin": "BTC",
                "side": "buy",
                "size": "0.1",
                "order_type": "market_order",
                "order": {
                    "product_id": SPOT_PRODUCT["id"],
                    "size": 0.1,
                    "side": "buy",
                    "order_type": "market_order",
                },
            },
            salt="portfolio.delta.spot.trade",
            compress=True,
        )
        response = self.client.post(
            "/portfolio/spot/confirm/",
            {"confirmation": token},
        )
        replay_response = self.client.post(
            "/portfolio/spot/confirm/",
            {"confirmation": token},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order submitted to Delta")
        self.assertContains(response, "987")
        self.assertEqual(replay_response.status_code, 400)
        self.assertContains(replay_response, "already used", status_code=400)
        self.assertEqual(place_order.call_count, 1)
        place_order.assert_called_once_with(
            {
                "product_id": SPOT_PRODUCT["id"],
                "size": 0.1,
                "side": "buy",
                "order_type": "market_order",
            }
        )
        cache.delete(
            "delta-spot-trade-"
            + hashlib.sha256(token.encode("utf-8")).hexdigest()
        )

    @patch.object(DeltaExchangeClient, "get_spot_products", return_value=[SPOT_PRODUCT])
    def test_posting_trade_confirmation_token_is_required_for_order(
        self, get_products
    ):
        response = self.client.post(
            "/portfolio/spot/confirm/",
            {"confirmation": "not-a-signed-confirmation"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "invalid or expired", status_code=400)
        get_products.assert_not_called()

    @patch.object(DeltaExchangeClient, "place_order")
    def test_trade_preview_is_rejected_from_non_local_clients(self, place_order):
        response = self.client.post(
            "/portfolio/spot/preview/",
            {
                "side": "buy",
                "product_id": str(SPOT_PRODUCT["id"]),
                "amount_mode": "coin",
                "amount": "0.001",
                "order_type": "market_order",
            },
            REMOTE_ADDR="192.0.2.1",
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "only enabled from this computer", status_code=403)
        place_order.assert_not_called()

    @override_settings(DELTA_API_KEY="test-key", DELTA_API_SECRET="test-secret")
    @patch("portfolio.delta_exchange.urlopen")
    def test_post_order_signature_covers_exact_json_body(self, urlopen_mock):
        urlopen_mock.return_value = io.BytesIO(
            b'{"success":true,"result":{"id":123,"state":"open"}}'
        )
        order = {
            "product_id": SPOT_PRODUCT["id"],
            "size": 0.001,
            "side": "sell",
            "order_type": "limit_order",
            "limit_price": "50000",
        }

        result = DeltaExchangeClient().place_order(order)

        request = urlopen_mock.call_args.args[0]
        body = request.data.decode("utf-8")
        timestamp = request.get_header("Timestamp")
        expected_signature = hmac.new(
            b"test-secret",
            f"POST{timestamp}/v2/orders{body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(result["id"], 123)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Signature"), expected_signature)

    @override_settings(DELTA_API_KEY="test-key", DELTA_API_SECRET="test-secret")
    @patch("portfolio.delta_exchange.urlopen")
    def test_positions_request_uses_all_margined_positions_endpoint(
        self, urlopen_mock
    ):
        urlopen_mock.return_value = io.BytesIO(
            b'{"success":true,"result":[{"product_symbol":"BTCUSD","size":"1"}]}'
        )

        positions = DeltaExchangeClient().get_positions()

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, (
            "https://api.india.delta.exchange/v2/positions/margined"
        ))
        self.assertEqual(positions[0]["product_symbol"], "BTCUSD")

    @override_settings(DELTA_API_KEY="test-key", DELTA_API_SECRET="test-secret")
    @patch("portfolio.delta_exchange.urlopen")
    def test_http_error_includes_delta_error_detail(self, urlopen_mock):
        urlopen_mock.side_effect = HTTPError(
            "https://api.india.delta.exchange/v2/positions/margined",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"success":false,"error":{"code":"invalid_parameter"}}'),
        )

        with self.assertRaisesRegex(
            DeltaExchangeAPIError,
            r"HTTP 400: invalid_parameter",
        ):
            DeltaExchangeClient().get_positions()

    @override_settings(DELTA_API_KEY="test-key", DELTA_API_SECRET="test-secret")
    @patch("portfolio.delta_exchange.urlopen")
    def test_insufficient_margin_error_explains_available_funds(
        self, urlopen_mock
    ):
        urlopen_mock.return_value = io.BytesIO(
            b'{"success":false,"error":{"code":"insufficient_margin",'
            b'"context":{"additional_margin_required":"12.5"}}}'
        )

        with self.assertRaisesRegex(
            DeltaExchangeAPIError,
            r"additional margin required: 12.5.*available INR balance",
        ):
            DeltaExchangeClient().get_wallet_balances()
