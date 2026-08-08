import pytest

from ped_hunter.parser import parse_line


def test_parse_loot_line():
    event = parse_line("2026-06-11 20:10:00 [System] [] You received Animal Oil Residue x (3) Value: 0.06 PED")
    assert event is not None
    assert event.kind == "loot"
    assert event.payload["quantity"] == 3
    assert event.payload["value"] == 0.06


@pytest.mark.parametrize(
    "line, expected_payload",
    [
        (
            "2026-06-20 08:30:13 [System]:Youinflicted43.8 points ofdamagewith costs of 0.1234 PED.",
            {"damage": 43.8},
        ),
        (
            "2026-06-20 08:30:13 [System] [] Critical hit - Additional damage! You inflicted 12.5 points of damage with costs of 0.0500 PED.",
            {"damage": 12.5},
        ),
        (
            "2026-06-20 08:30:14 [System] [] The creature Dodged your attack.",
            {"dodged": True},
        ),
    ],
)
def test_parse_combat_shot_lines_capture_damage_or_shot_consumption_fields(line, expected_payload):
    event = parse_line(line)

    assert event is not None
    assert event.kind == "combat"
    for key, value in expected_payload.items():
        assert event.payload[key] == value



def test_parse_compact_loot_line_with_brackets_and_source():
    event = parse_line("2026-06-20 08:30:06 [System]:Youreceived[Shrapnel] x (288613)Value: 28.86 PED from (Big Bulk Gen 10)")

    assert event is not None
    assert event.kind == "loot"
    assert event.payload["item_name"] == "Shrapnel"
    assert event.payload["quantity"] == 288613
    assert event.payload["value"] == 28.86


def test_channel_message_quoting_system_receipt_is_not_personal_loot():
    event = parse_line(
        "2026-08-08 04:56:52 [Rookie] [B0b The South] [System]: "
        "You received [NeoPsion 20 Mindforce Implant] x (1) Value: 18.84 PED"
    )

    assert event is not None
    assert event.kind == "chat"
    assert event.payload["channel"] == "Rookie"
    assert event.payload["speaker"] == "B0b The South"


def test_parse_compact_damage_line_with_cost():
    event = parse_line("2026-06-20 08:30:13 [System]:Youinflicted43.8 points ofdamagewith costs of 0.1234 PED.")

    assert event is not None
    assert event.kind == "combat"
    assert event.payload["damage"] == 43.8


def test_refiner_and_conversion_outputs_are_not_loot():
    lines = [
        "2026-06-20 08:30:06 [System] [] You received Oil x (107) Value: 2.14 PED",
        "2026-06-20 08:30:13 [System] [] You received Lysterium Ingot x (267) Value: 8.01 PED",
        "2026-06-20 04:10:06 [System] [] You received Universal Ammo x (2930954) Value: 293.09 PED",
    ]

    assert [parse_line(line) for line in lines] == [None, None, None]


def test_refiner_filter_does_not_block_oil_residue_loot():
    event = parse_line("2026-06-11 20:10:00 [System] [] You received Animal Oil Residue x (3) Value: 0.06 PED")

    assert event is not None
    assert event.kind == "loot"
    assert event.payload["item_name"] == "Animal Oil Residue"


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


def test_parse_item_damaged_marker():
    event = parse_line("2026-06-24 07:17:49 [System] [] The item is damaged.")

    assert event is not None
    assert event.kind == "equipment"
    assert event.payload == {"item_damaged": True}


def test_parse_repair_success_marker():
    event = parse_line("2026-06-24 07:18:49 [System] [] Item(s) repaired successfully")

    assert event is not None
    assert event.kind == "repair"
    assert event.payload["resets_durability"] is True
    assert event.payload["repair_reset"] is True


def test_parse_global_chat_and_channel_messages():
    global_event = parse_line("2026-07-01 23:47:21 [Globals] [] Bulletproof Turk Turk killed a creature (Void Umbranoid Acolyte) with a value of 58 PED!")
    channel_event = parse_line("2026-07-01 23:47:01 [#disi] [Doomsdib Doomsdib Doomsdib] yea im slow grinding it. and F2P makes it harder lol")

    assert global_event is not None
    assert global_event.kind == "global"
    assert global_event.payload == {
        "channel": "Globals",
        "speaker": "",
        "message": "Bulletproof Turk Turk killed a creature (Void Umbranoid Acolyte) with a value of 58 PED!",
    }

    assert channel_event is not None
    assert channel_event.kind == "chat"
    assert channel_event.payload == {
        "channel": "#disi",
        "speaker": "Doomsdib Doomsdib Doomsdib",
        "message": "yea im slow grinding it. and F2P makes it harder lol",
    }
