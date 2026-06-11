from ped_hunter.catalog import Catalog


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
