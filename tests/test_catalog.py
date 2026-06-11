from ped_hunter.catalog import Catalog


def test_frontier_alias_resolves():
    catalog = Catalog.load()
    weapon = catalog.find_weapon("Frontier Rifle")
    assert weapon is not None
    assert weapon.name == "EWE LC-100 Frontier"
    assert weapon.category == "Carbine"
