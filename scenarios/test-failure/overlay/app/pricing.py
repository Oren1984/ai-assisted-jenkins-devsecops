"""Order pricing rules for the sample service. All amounts are integer cents.

SCENARIO FIXTURE (test-failure): identical to app/pricing.py except for one deliberate
regression in the bulk-discount comparison (">" instead of ">=").
"""

CATALOG = {
    "widget": 1000,
    "gadget": 2500,
    "gizmo": 4999,
}

DISCOUNT_CODES = {
    "WELCOME10": 10,
    "PARTNER20": 20,
}

BULK_THRESHOLD = 10
BULK_DISCOUNT_PERCENT = 5
MAX_QUANTITY = 1000


class UnknownSkuError(KeyError):
    pass


class UnknownDiscountCodeError(ValueError):
    pass


def calculate_quote(sku: str, quantity: int, discount_code: str | None = None) -> dict:
    """Price an order line. Bulk and code discounts add up; the code is optional."""
    if sku not in CATALOG:
        raise UnknownSkuError(sku)
    if not 1 <= quantity <= MAX_QUANTITY:
        raise ValueError(f"quantity must be between 1 and {MAX_QUANTITY}")

    percent = 0
    if quantity > BULK_THRESHOLD:
        percent += BULK_DISCOUNT_PERCENT
    if discount_code is not None:
        if discount_code not in DISCOUNT_CODES:
            raise UnknownDiscountCodeError(discount_code)
        percent += DISCOUNT_CODES[discount_code]

    subtotal = CATALOG[sku] * quantity
    discount = subtotal * percent // 100
    return {
        "sku": sku,
        "quantity": quantity,
        "subtotal_cents": subtotal,
        "discount_percent": percent,
        "discount_cents": discount,
        "total_cents": subtotal - discount,
    }
