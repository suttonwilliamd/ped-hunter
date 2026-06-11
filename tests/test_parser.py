from ped_hunter.parser import parse_line


def test_parse_loot_line():
    event = parse_line("2026-06-11 20:10:00 [System] [] You received Animal Oil Residue x (3) Value: 0.06 PED")
    assert event is not None
    assert event.kind == "loot"
    assert event.payload["quantity"] == 3
    assert event.payload["value"] == 0.06
