from ped_hunter.catalog import Catalog
from ped_hunter import cli
from ped_hunter.cli import _normalize


def test_frontier_alias_resolves():
    catalog = Catalog.load()
    weapon = catalog.find_weapon("Frontier Rifle")
    assert weapon is not None
    assert weapon.name == "Frontier Hunting Rifle"
    assert weapon.category == "Rifle"
    assert weapon.ammo == 100
    assert weapon.decay == 0.0002


def test_frontier_combat_knife_and_adjusted_resolve():
    catalog = Catalog.load()
    knife = catalog.find_weapon("Frontier Combat Knife")
    adjusted = catalog.find_weapon("Frontier Combat Knife, Adjusted")
    assert knife is not None
    assert knife.name == "Frontier Combat Knife"
    assert knife.category == "Knife"
    assert knife.ammo == 49
    assert knife.decay == 0.0002
    assert adjusted is not None
    assert adjusted.name == "Frontier Combat Knife, Adjusted"
    assert adjusted.category == "Knife"
    assert adjusted.ammo == 98
    assert adjusted.decay == 0.0002


def test_frontier_hunting_rifle_is_distinct_from_ewe_frontier():
    catalog = Catalog.load()
    frontier = catalog.find_weapon("Frontier Rifle")
    ewe = catalog.find_weapon("EWE LC-100 Frontier")
    assert frontier is not None
    assert ewe is not None
    assert frontier.name != ewe.name


def test_set_p1_and_set_p2_weapons_are_in_catalog():
    catalog = Catalog.load()

    p1 = catalog.find_weapon("SET-P1 Civilian Sidearm, Adjusted")
    p2 = catalog.find_weapon("SET-P2 Scout Sidearm (L)")

    assert p1 is not None
    assert p1.name == "SET-P1 Civilian Sidearm, Adjusted"
    assert p1.category == "Pistol"
    assert p1.ammo == 7
    assert p1.decay == 0.001
    assert p1.max_tt == 0.1

    assert p2 is not None
    assert p2.name == "SET-P2 Scout Sidearm (L)"
    assert p2.category == "Pistol"
    assert p2.ammo == 20
    assert p2.decay == 0.001
    assert p2.max_tt == 0.2


def test_cli_seed_normalization_keeps_frontier_hunting_rifle_distinct():
    normalized = _normalize(
        {
            "weapons.json": {
                "data": {
                    "EWE LC-100 Frontier": {"type": "Carbine", "ammo": 1030, "decay": 0.0087}
                }
            },
            "attachments.json": {"data": {}},
            "scopes.json": {"data": {}},
            "sights.json": {"data": {}},
            "resources.json": {"data": {}},
            "crafting.json": {"data": {}},
        }
    )

    weapons = {item["name"]: item for item in normalized["weapons.json"]["items"]}
    assert normalized["aliases.json"] == {"Frontier Rifle": "Frontier Hunting Rifle"}
    assert weapons["EWE LC-100 Frontier"]["aliases"] == []
    assert weapons["Frontier Combat Knife"]["aliases"] == []
    assert weapons["Frontier Combat Knife, Adjusted"]["aliases"] == []
    assert weapons["Frontier Hunting Rifle"]["aliases"] == ["Frontier Rifle"]


def test_mining_finders_are_distinct_catalog_records():
    catalog = Catalog.load()
    expected = {
        "Locator MK1 (L)": (10, 0.00239, 0.1, 0.002),
        "A.R.C. Finder 0001 (L)": (100, 0.0025, 0.1, 0.00251),
        "Finder F-101": (100, 0.01, 5.5, 0.165),
        "Finder F-105": (100, 0.0205, 82.0, 2.46),
        "Finder F-213 (L)": (100, 0.0166, 201.2, 6.036),
    }
    for name, (ammo, decay, max_tt, min_tt) in expected.items():
        finder = catalog.find_weapon(name)
        assert finder is not None
        assert finder.category == "Mining Finder"
        assert (finder.ammo, finder.decay, finder.max_tt, finder.min_tt) == (ammo, decay, max_tt, min_tt)
        assert finder.source_name == "Entropia Nexus"


def test_normalize_preserves_mining_finder_supplemental_records():
    normalized = _normalize({
        "weapons.json": {"data": {}},
        "attachments.json": {"data": {}},
        "scopes.json": {"data": {}},
        "sights.json": {"data": {}},
        "resources.json": {"data": {}},
        "crafting.json": {"data": {}},
    })
    by_name = {item["name"]: item for item in normalized["weapons.json"]["items"]}
    assert len([item for item in by_name.values() if item["category"] == "Mining Finder"]) == 11
    assert by_name["Finder F-102"]["decay"] == 0.0115


def test_cli_no_args_launches_gui_by_default(monkeypatch):
    launched = False

    def fake_launch_gui():
        nonlocal launched
        launched = True
        return 0

    monkeypatch.setattr(cli, "_launch_gui", fake_launch_gui)

    assert cli.main([]) == 0
    assert launched
