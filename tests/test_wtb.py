import pytest

from ped_hunter.app import _wtb_listings
from ped_hunter.parser import extract_wtb_segments
from ped_hunter.storage import Store


def test_extract_wtb_segments_from_mixed_trade_message():
    listings = extract_wtb_segments("#trade", "Alice", "WTB Mod Merc | WTS EP-41")

    assert len(listings) == 1
    assert listings[0]["channel"] == "#trade"
    assert listings[0]["speaker"] == "Alice"
    assert listings[0]["want"] == "Mod Merc"


@pytest.mark.parametrize("marker", ["b/o", "B/O", "b/O", "B/o"])
def test_extract_wtb_segments_preserves_buyout_price(marker):
    listings = extract_wtb_segments(
        "#trade", "Alice", f"WTB 2x Mod Merc {marker} TT+10% / WTS EP-41"
    )

    assert len(listings) == 1
    assert listings[0]["want"] == "Mod Merc"
    assert listings[0]["quantity"] == 2
    assert listings[0]["price"] == "TT+10%"
    assert listings[0]["segment"] == f"WTB 2x Mod Merc {marker} TT+10%"


@pytest.mark.parametrize("separator", ["/", " / ", "|", ";", "•", "—"])
@pytest.mark.parametrize("first_item,second_item", [("Mod Merc", "EP-41"), ("Club", "Oil")])
def test_extract_wtb_segments_keeps_ordinary_listing_separators(
    separator, first_item, second_item
):
    listings = extract_wtb_segments(
        "#trade", "Alice", f"WTB {first_item}{separator}WTB {second_item}"
    )

    assert [listing["want"] for listing in listings] == [first_item, second_item]
    assert [listing["price"] for listing in listings] == [None, None]


@pytest.mark.parametrize("marker", ["@", "b/o", "B/O", "bo"])
def test_wtb_listings_filters_trade_channels_and_parses_qty_and_price(tmp_path, marker):
    store = Store(tmp_path / "ped.sqlite3")
    session_id = store.start_session("hunt")
    store.add_event(
        session_id,
        {
            "kind": "chat",
            "timestamp": "2026-07-05 10:00:00",
            "raw_message": f"[#trade] [Alice] WTB 2x Mod Merc {marker} TT+10% | WTS something else",
            "payload": {
                "channel": "#trade",
                "speaker": "Alice",
                "message": f"WTB 2x Mod Merc {marker} TT+10% | WTS something else",
            },
        },
    )
    store.add_event(
        session_id,
        {
            "kind": "chat",
            "timestamp": "2026-07-05 10:01:00",
            "raw_message": "[#local] [Bob] WTB not shown here",
            "payload": {
                "channel": "#local",
                "speaker": "Bob",
                "message": "WTB not shown here",
            },
        },
    )

    listings = _wtb_listings(store, limit=20)

    assert len(listings) == 1
    listing = listings[0]
    assert listing["channel"] == "#trade"
    assert listing["speaker"] == "Alice"
    assert listing["want"] == "Mod Merc"
    assert listing["quantity"] == 2
    assert listing["price"] == "TT+10%"
