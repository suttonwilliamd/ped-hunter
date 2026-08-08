from ped_hunter.currency import format_money


def test_usd_mode_moves_the_ped_decimal_one_place():
    assert format_money(100.0, "USD") == "$10.00"
    assert format_money(12.5, "USD") == "$1.25"
    assert format_money(12.5, "USD", signed=True) == "+$1.25"
    assert format_money(-12.5, "USD", signed=True) == "-$1.25"


def test_ped_mode_remains_available():
    assert format_money(12.5, "PED") == "12.50 PED"
    assert format_money(-12.5, "PED", signed=True) == "-12.50 PED"