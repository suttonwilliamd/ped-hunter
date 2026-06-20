from ped_hunter.parser import parse_line


def test_parse_loot_line():
    event = parse_line("2026-06-11 20:10:00 [System] [] You received Animal Oil Residue x (3) Value: 0.06 PED")
    assert event is not None
    assert event.kind == "loot"
    assert event.payload["quantity"] == 3
    assert event.payload["value"] == 0.06


def test_parse_skips_universal_ammo_conversion():
    event = parse_line("2026-06-20 04:10:06 [System] [] You received Universal Ammo x (2930954) Value: 293.09 PED")
    assert event is None


def test_parse_lootnanny_skill_gain_formats():
    lines = [
        ("2026-06-11 20:10:01 [System] [] You have gained 0.1234 experience in your Anatomy skill", "Anatomy", 0.1234),
        ("2026-06-11 20:10:02 [System] [] You have gained 0.2500 Laser Weaponry Technology", "Laser Weaponry Technology", 0.25),
        ("2026-06-11 20:10:03 [System] [] You gained 0.5000 Aim", "Aim", 0.5),
        ("2026-06-11 20:10:04 [System] [] Your Rifle has improved by 0.7500", "Rifle", 0.75),
    ]
    for line, skill, xp in lines:
        event = parse_line(line)
        assert event is not None
        assert event.kind == "skill"
        assert event.payload == {"skill": skill, "xp": xp}
