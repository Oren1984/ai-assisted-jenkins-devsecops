import pytest

from app.pricing import UnknownDiscountCodeError, UnknownSkuError, calculate_quote


def test_single_item_has_no_discount():
    quote = calculate_quote("widget", 1)
    assert quote["discount_cents"] == 0
    assert quote["total_cents"] == 1000


def test_below_bulk_threshold_has_no_discount():
    assert calculate_quote("widget", 9)["total_cents"] == 9000


def test_bulk_discount_applies_at_threshold():
    # 10 widgets = 10000 cents, 5% bulk discount = 500
    assert calculate_quote("widget", 10)["total_cents"] == 9500


def test_bulk_and_code_discounts_add_up():
    quote = calculate_quote("widget", 20, "PARTNER20")
    assert quote["discount_percent"] == 25
    assert quote["total_cents"] == 15000


def test_unknown_sku_raises():
    with pytest.raises(UnknownSkuError):
        calculate_quote("sprocket", 1)


def test_unknown_discount_code_raises():
    with pytest.raises(UnknownDiscountCodeError):
        calculate_quote("widget", 1, "NOPE")


@pytest.mark.parametrize("quantity", [0, -1, 1001])
def test_quantity_out_of_range_raises(quantity):
    with pytest.raises(ValueError):
        calculate_quote("widget", quantity)
