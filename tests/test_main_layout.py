"""Main-window logic that needs no window: size classes, table sorting, list filtering, display text."""
from ped_hunter.app import EVENT_FILTERS, HELP_SECTIONS, _listing_order, _short_time, _small_money
from ped_hunter.responsive import DEFAULT_SIZE, MIN_SIZE, SizeClass, chart_height, classify
from ped_hunter.search_select import filter_values
from ped_hunter.table_sort import number


def test_size_classes_follow_the_thresholds():
    assert classify(*DEFAULT_SIZE) == SizeClass("regular")
    assert classify(1180, 780) == SizeClass("wide")
    assert classify(700, 700) == SizeClass("compact")            # narrow: run bar and drawer
    assert classify(1200, 560) == SizeClass("compact")           # short: same, at any width
    assert classify(*MIN_SIZE) == SizeClass("compact", split=True, tight=True)
    assert classify(560, 900) == SizeClass("compact", tight=True)


def test_size_class_does_not_flicker_at_a_threshold():
    wide = classify(1000, 700)
    assert wide.name == "wide"
    assert classify(990, 700, wide).name == "wide"               # within the slack: stays
    assert classify(980, 700, wide).name == "regular"
    assert classify(995, 700, SizeClass("regular")).name == "regular"   # must reach the threshold to go back
    compact = classify(715, 700)
    assert classify(722, 700, compact).name == "regular"


def test_chart_height_leaves_room_for_the_tables():
    assert chart_height(1000, SizeClass("wide")) == 300
    assert chart_height(300, SizeClass("compact")) == 190
    assert chart_height(400, SizeClass("compact", split=True)) == 0


def test_sort_numbers_read_displayed_money_percent_and_counts():
    assert [number(text) for text in ("-$1.69", "+$46.74", "95.09%", "1,106", "12.50 PED", "-2.20 PED")] == [
        -1.69, 46.74, 95.09, 1106.0, 12.5, -2.2]
    assert number("—") is None and number("") is None


def test_selector_filter_matches_every_word_and_puts_prefixes_first():
    values = ["Frontier Hunting Rifle", "ZX Eagle Eye", "ZX Sinkadus", "Adjusted Frontier Rifle", "Omegaton ZX"]
    assert filter_values(values, "") == values
    assert filter_values(values, "zx s") == ["ZX Sinkadus"]
    assert filter_values(values, "FRON rif") == ["Frontier Hunting Rifle", "Adjusted Frontier Rifle"]
    assert filter_values(values, "zx") == ["ZX Eagle Eye", "ZX Sinkadus", "Omegaton ZX"]
    assert filter_values(values, "nothing") == []


def test_display_text_helpers():
    assert _short_time("2026-11-14T03:09:18") in ("Nov 14 03:09", "Nov 14 2026 03:09")
    assert _short_time("2026-11-14T03:09:18", seconds=True, today="2026-11-14") == "03:09:18"
    assert _short_time("not a date") == "not a date"
    assert _small_money(0.0325, "USD") == "$0.0033" and _small_money(0.0325, "PED") == "0.03 PED"
    assert _small_money(12.5, "USD") == "$1.25" and _small_money(0.0, "USD") == "$0.00"
    assert sorted(["0x0F38 (L)", "Zeta", "alpha"], key=_listing_order) == ["alpha", "Zeta", "0x0F38 (L)"]


def test_user_facing_help_has_no_implementation_talk():
    text = " ".join(heading + " " + body for heading, body in HELP_SECTIONS).casefold()
    for word in ("lootnanny", "loot nanny", "pyqt", "sqlite", "database", "tkinter"):
        assert word not in text
    assert "chat" not in EVENT_FILTERS and EVENT_FILTERS["Everything"] is None
