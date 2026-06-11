from ped_hunter.catalog import Catalog
from ped_hunter.cli import _normalize


def test_frontier_alias_resolves():
    catalog = Catalog.load()
    weapon = catalog.find_weapon("Frontier Rifle")
    assert weapon is not None
    assert weapon.name == "Frontier Hunting Rifle"
    assert weapon.category == "Rifle"
    assert weapon.ammo == 100
    assert weapon.decay == 0.0002


def test_frontier_hunting_rifle_is_distinct_from_ewe_frontier():
    catalog = Catalog.load()
    frontier = catalog.find_weapon("Frontier Rifle")
    ewe = catalog.find_weapon("EWE LC-100 Frontier")
    assert frontier is not None
    assert ewe is not None
    assert frontier.name != ewe.name


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
    assert weapons["Frontier Hunting Rifle"]["aliases"] == ["Frontier Rifle"]
