import hashlib

from django.contrib.auth import login
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .delta_exchange import DeltaExchangeAPIError, DeltaExchangeClient
from .market_data import MarketDataClient, MarketDataError
from .spot_trading import (
    SpotOrderValidationError,
    build_spot_order_preview,
    validate_confirmed_order,
)


TRADE_CONFIRMATION_SALT = "portfolio.delta.spot.trade"
TRADE_CONFIRMATION_MAX_AGE = 300


def signup(request):
    if not settings.ALLOW_SIGNUPS:
        raise Http404
    if request.user.is_authenticated:
        return redirect("portfolio")

    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("portfolio")

    return render(request, "registration/signup.html", {"form": form})


def _first_present(data, *field_names):
    for field_name in field_names:
        value = data.get(field_name)
        if value is not None:
            return value
    return "—"


def dashboard(request):
    query = request.GET.get("q", "").strip()
    context = {"query": query, "coins": [], "searched": bool(query), "error": None}
    if query:
        try:
            context["coins"] = MarketDataClient().search_coins_inr(query)
        except MarketDataError as exc:
            context["error"] = str(exc)

    return render(request, "portfolio/dashboard.html", context)


@login_required
def portfolio(request):
    context = {
        "balances": [],
        "positions": [],
        "spot_products": [],
        "spot_products_error": None,
        "balances_error": None,
        "positions_error": None,
    }
    client = DeltaExchangeClient()

    try:
        for product in client.get_spot_products():
            specs = product.get("product_specs")
            if (
                isinstance(specs, dict)
                and product.get("contract_type") == "spot"
                and product.get("state") == "live"
                and product.get("trading_status") == "operational"
                and not specs.get("only_reduce_only_orders_allowed", False)
            ):
                context["spot_products"].append(product)
    except DeltaExchangeAPIError as exc:
        context["spot_products_error"] = str(exc)

    try:
        balances = client.get_wallet_balances()
        context["balances"] = [
            {
                "asset": _first_present(
                    balance, "asset_symbol", "asset", "symbol"
                ),
                "available": _first_present(
                    balance, "available_balance", "available"
                ),
                "total": _first_present(
                    balance, "total_balance", "balance"
                ),
            }
            for balance in balances
        ]
    except DeltaExchangeAPIError as exc:
        context["balances_error"] = str(exc)

    try:
        positions = client.get_positions()
        context["positions"] = [
            {
                "product": _first_present(
                    position, "product_symbol", "symbol"
                ),
                "size": _first_present(position, "size"),
                "entry_price": _first_present(position, "entry_price"),
                "mark_price": _first_present(position, "mark_price"),
                "unrealized_pnl": _first_present(
                    position, "unrealized_pnl"
                ),
            }
            for position in positions
        ]
    except DeltaExchangeAPIError as exc:
        context["positions_error"] = str(exc)

    return render(request, "portfolio/portfolio.html", context)


def _trade_error(request, message, status=400):
    return render(
        request,
        "portfolio/trade_error.html",
        {"error": message},
        status=status,
    )


def _trade_request_is_local(request):
    return request.META.get("REMOTE_ADDR") in {"127.0.0.1", "::1"}


@require_POST
@login_required
def spot_trade_preview(request):
    if not _trade_request_is_local(request):
        return _trade_error(
            request,
            "Spot trading is only enabled from this computer.",
            status=403,
        )

    try:
        preview = build_spot_order_preview(
            DeltaExchangeClient(), request.POST
        )
    except (DeltaExchangeAPIError, SpotOrderValidationError) as exc:
        return _trade_error(request, str(exc))

    token = signing.dumps(preview, salt=TRADE_CONFIRMATION_SALT, compress=True)
    return render(
        request,
        "portfolio/confirm_trade.html",
        {"preview": preview, "confirmation": token},
    )


@require_POST
@login_required
def spot_trade_confirm(request):
    if not _trade_request_is_local(request):
        return _trade_error(
            request,
            "Spot trading is only enabled from this computer.",
            status=403,
        )

    token = request.POST.get("confirmation", "")
    if not token or len(token) > 8192:
        return _trade_error(request, "The trade confirmation is invalid.")

    try:
        preview = signing.loads(
            token,
            salt=TRADE_CONFIRMATION_SALT,
            max_age=TRADE_CONFIRMATION_MAX_AGE,
        )
        order = validate_confirmed_order(DeltaExchangeClient(), preview)
    except signing.BadSignature:
        return _trade_error(
            request,
            "This trade preview is invalid or expired. Create a new preview.",
        )
    except (DeltaExchangeAPIError, SpotOrderValidationError) as exc:
        return _trade_error(request, str(exc))

    token_key = "delta-spot-trade-" + hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()
    if not cache.add(token_key, True, timeout=TRADE_CONFIRMATION_MAX_AGE):
        return _trade_error(
            request,
            "This confirmation was already used. Check Delta order history "
            "before creating another order.",
        )

    try:
        result = DeltaExchangeClient().place_order(order)
    except DeltaExchangeAPIError as exc:
        return _trade_error(request, str(exc), status=502)

    return render(
        request,
        "portfolio/trade_result.html",
        {
            "order_id": result.get("id"),
            "order_status": result.get("state") or result.get("status"),
            "symbol": preview["symbol"],
            "side": preview["side"],
            "size": preview["size"],
            "coin": preview["coin"],
        },
    )