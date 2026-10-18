from decimal import Decimal, InvalidOperation, ROUND_DOWN

from .delta_exchange import DeltaExchangeAPIError


class SpotOrderValidationError(ValueError):
    pass


def _positive_decimal(value, field_name):
    if not isinstance(value, str) or len(value) > 40:
        raise SpotOrderValidationError(f"Enter a valid {field_name}.")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise SpotOrderValidationError(f"Enter a valid {field_name}.") from None
    if not number.is_finite() or number <= 0:
        raise SpotOrderValidationError(f"{field_name.capitalize()} must be greater than zero.")
    if number.adjusted() > 30:
        raise SpotOrderValidationError(f"{field_name.capitalize()} is too large.")
    return number


def build_spot_order_preview(client, form_data):
    side = form_data.get("side", "")
    if side not in {"buy", "sell"}:
        raise SpotOrderValidationError("Choose buy or sell.")

    try:
        product_id = int(form_data.get("product_id", ""))
    except (TypeError, ValueError):
        raise SpotOrderValidationError("Choose a valid Delta spot market.") from None

    products = client.get_spot_products()
    product = next(
        (
            item
            for item in products
            if isinstance(item, dict) and item.get("id") == product_id
        ),
        None,
    )
    if not product:
        raise SpotOrderValidationError(
            "That market is not an available Delta spot product."
        )
    if (
        product.get("contract_type") != "spot"
        or product.get("state") != "live"
        or product.get("trading_status") != "operational"
    ):
        raise SpotOrderValidationError(
            "That Delta spot market is not currently available for trading."
        )

    order_type = form_data.get("order_type", "")
    if order_type not in {"market_order", "limit_order"}:
        raise SpotOrderValidationError("Choose a market or limit order.")

    amount_mode = form_data.get("amount_mode", "")
    if amount_mode not in {"coin", "inr"}:
        raise SpotOrderValidationError("Choose coin quantity or INR amount.")

    amount = _positive_decimal(form_data.get("amount", ""), "amount")
    limit_price = None
    if order_type == "limit_order":
        limit_price = _positive_decimal(
            form_data.get("limit_price", ""), "limit price"
        )
        tick_size = _positive_decimal(product.get("tick_size"), "market tick size")
        if limit_price % tick_size:
            raise SpotOrderValidationError(
                f"Limit price must be a multiple of the market tick size ({tick_size})."
            )
        reference_price = limit_price
    else:
        ticker = client.get_spot_ticker(product["symbol"])
        reference_price = _positive_decimal(
            ticker.get("spot_price"), "current reference price"
        )

    if amount_mode == "inr":
        requested_size = amount / reference_price
    else:
        requested_size = amount

    specs = product.get("product_specs")
    if not isinstance(specs, dict):
        raise DeltaExchangeAPIError(
            "Delta did not return order size requirements for this market."
        )
    try:
        precision = int(specs["underlying_precision"])
        minimum_size = Decimal(str(specs["min_order_size"]))
        maximum_size = Decimal(str(specs["max_order_size"]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        raise DeltaExchangeAPIError(
            "Delta returned invalid order size requirements for this market."
        ) from None
    if precision < 0 or precision > 18:
        raise DeltaExchangeAPIError(
            "Delta returned an unsupported size precision for this market."
        )
    if requested_size > maximum_size:
        raise SpotOrderValidationError(
            f"Order size cannot exceed {maximum_size} {product['contract_unit_currency']} per order."
        )
    if requested_size.adjusted() < -30:
        raise SpotOrderValidationError(
            "The requested amount is too small for this market."
        )

    size_quantum = Decimal(1).scaleb(-precision)
    size = requested_size.quantize(size_quantum, rounding=ROUND_DOWN)
    if size <= 0:
        raise SpotOrderValidationError(
            "The requested amount is too small for this market's coin precision."
        )
    if size < minimum_size:
        raise SpotOrderValidationError(
            f"Order size must be at least {minimum_size} {product['contract_unit_currency']}."
        )
    if size > maximum_size:
        raise SpotOrderValidationError(
            f"Order size cannot exceed {maximum_size} {product['contract_unit_currency']} per order."
        )

    order = {
        "product_id": product_id,
        "size": float(size),
        "side": side,
        "order_type": order_type,
    }
    if limit_price is not None:
        order["limit_price"] = format(limit_price, "f")

    return {
        "order": order,
        "symbol": product["symbol"],
        "coin": product["contract_unit_currency"],
        "side": side,
        "order_type": order_type,
        "size": format(size, "f"),
        "limit_price": format(limit_price, "f") if limit_price is not None else None,
        "estimated_inr": format(size * reference_price, "f"),
        "reference_price": format(reference_price, "f"),
        "amount_mode": amount_mode,
        "entered_amount": format(amount, "f"),
    }


def validate_confirmed_order(client, preview):
    order = preview.get("order")
    if not isinstance(order, dict):
        raise SpotOrderValidationError("The trade confirmation is invalid.")

    try:
        product_id = int(order["product_id"])
        size = Decimal(str(order["size"]))
        side = order["side"]
        order_type = order["order_type"]
    except (KeyError, InvalidOperation, TypeError, ValueError):
        raise SpotOrderValidationError("The trade confirmation is invalid.") from None

    if (
        not size.is_finite()
        or size <= 0
        or side not in {"buy", "sell"}
        or order_type not in {"market_order", "limit_order"}
    ):
        raise SpotOrderValidationError("The trade confirmation is invalid.")

    products = client.get_spot_products()
    product = next(
        (
            item
            for item in products
            if isinstance(item, dict) and item.get("id") == product_id
        ),
        None,
    )
    if (
        not product
        or product.get("contract_type") != "spot"
        or product.get("state") != "live"
        or product.get("trading_status") != "operational"
    ):
        raise SpotOrderValidationError(
            "This spot market is no longer available. Create a new preview."
        )
    specs = product.get("product_specs")
    if not isinstance(specs, dict):
        raise DeltaExchangeAPIError(
            "Delta did not return order size requirements for this market."
        )
    try:
        precision = int(specs["underlying_precision"])
        minimum_size = Decimal(str(specs["min_order_size"]))
        maximum_size = Decimal(str(specs["max_order_size"]))
        if precision < 0 or precision > 18:
            raise ValueError("unsupported precision")
        normalized_size = size.quantize(Decimal(1).scaleb(-precision))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        raise DeltaExchangeAPIError(
            "Delta returned invalid order size requirements for this market."
        ) from None
    if (
        normalized_size != size
        or size < minimum_size
        or size > maximum_size
    ):
        raise SpotOrderValidationError(
            "The confirmed size no longer matches Delta's market requirements."
        )

    if order_type == "limit_order":
        try:
            price = Decimal(str(order["limit_price"]))
            tick_size = Decimal(str(product["tick_size"]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            raise SpotOrderValidationError(
                "The confirmed limit price is invalid."
            ) from None
        if (
            not price.is_finite()
            or price <= 0
            or tick_size <= 0
            or price % tick_size
        ):
            raise SpotOrderValidationError(
                "The confirmed limit price no longer matches Delta's market rules."
            )
    elif "limit_price" in order:
        raise SpotOrderValidationError("The trade confirmation is invalid.")

    return order
