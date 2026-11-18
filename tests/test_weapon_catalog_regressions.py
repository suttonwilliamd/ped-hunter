"""Weapon catalog regressions: the exact Hyperion Aurora PF-1 and unintended catalog losses."""
import importlib.util
import json
from pathlib import Path

import pytest

from ped_hunter.catalog import Catalog
from ped_hunter.search_select import filter_values

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("data") / "weapon_catalog_baseline.txt"
PF1 = "Hyperion Aurora PF-1 (L)"
PF1_IMPROVED = "Hyperion Aurora PF-1, Improved (L)"


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load()


@pytest.fixture(scope="module")
def picker_names(catalog):
    return sorted(catalog.weapons)  # what the setup builder's weapon picker lists


def test_pf1_base_and_improved_are_distinct_with_nexus_cost_inputs(catalog):
    # Entropia Nexus weapons/3049 and /2948: Decay 0.049 / 0.069 PEC, AmmoBurn 118 / 165
    # Weapon Cells, MaxTT 5 / 8 PED. Catalog decay is PED; ammo is raw units (1 = 0.0001 PED).
    base, improved = catalog.weapons[PF1], catalog.weapons[PF1_IMPROVED]
    assert (base.category, base.ammo, base.decay, base.max_tt) == ("Power Fist", 118, pytest.approx(0.00049), 5.0)
    assert base.cost_per_shot == pytest.approx(0.01229)
    assert (improved.category, improved.ammo, improved.decay, improved.max_tt) == (
        "Power Fist", 165, pytest.approx(0.00069), 8.0)
    assert improved.cost_per_shot == pytest.approx(0.01719)


def test_pf1_is_found_by_picker_search_and_resolves_exactly(catalog, picker_names):
    assert filter_values(picker_names, "pf-1")[:2] == [PF1, PF1_IMPROVED]
    assert filter_values(picker_names, "aurora pf-1 improved") == [PF1_IMPROVED]
    assert catalog.exact_weapon_name(PF1) == PF1
    assert catalog.exact_weapon_name("hyperion aurora pf-1, improved (l)") == PF1_IMPROVED
    # Partial text never stands in for a variant at save time.
    assert catalog.exact_weapon_name("Hyperion Aurora PF-1") is None
    assert catalog.exact_weapon_name("PF-1") is None


def test_names_differing_only_in_case_each_resolve_to_themselves(catalog):
    for name in ("RIP M6600, Modified", "Rip M6600, Modified"):
        assert catalog.exact_weapon_name(name) == name
        assert catalog.find_weapon(name).name == name


def test_no_shipped_weapon_is_lost_or_unselectable(catalog, picker_names):
    baseline = [line for line in BASELINE.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")]
    assert len(baseline) >= 3163
    missing = [name for name in baseline if name not in catalog.weapons]
    assert not missing, f"weapons dropped from the catalog: {missing[:20]}"
    picker = set(picker_names)
    unselectable = [name for name in baseline
                    if name not in picker or catalog.exact_weapon_name(name) != name]
    assert not unselectable, f"weapons no longer selectable: {unselectable[:20]}"


def test_resync_cannot_drop_curated_or_nexus_weapons():
    """A refresh rebuilds weapons.json from LootNanny + the sync tool's seeds + supplements.

    Anything not from LootNanny must come back from one of the other two, with the same
    cost inputs, or the next refresh would silently delete it.
    """
    spec = importlib.util.spec_from_file_location("sync_for_audit", ROOT / "tools" / "sync_legacy_data.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    seeds = [sync.FRONTIER_COMBAT_KNIFE, sync.FRONTIER_COMBAT_KNIFE_ADJUSTED, sync.FRONTIER_HUNTING_RIFLE,
             sync.FRONTIER_HUNTING_RIFLE_ADJUSTED, sync.SET_P1_CIVILIAN_SIDEARM_ADJUSTED,
             sync.SET_P2_SCOUT_SIDEARM, sync.SET_COMBAT_BLADE, *sync.MINING_FINDERS]
    supplements = json.loads((ROOT / "data/catalog/weapon_supplements.json").read_text(encoding="utf-8"))["items"]
    # Supplements are merged last, so they win over a same-named seed.
    sources = {item["name"].casefold(): item for item in [*seeds, *supplements]}
    shipped = json.loads((ROOT / "data/catalog/weapons.json").read_text(encoding="utf-8"))["items"]
    orphans = []
    for item in shipped:
        if item["source_name"] == "LootNanny legacy seed":
            continue
        source = sources.get(item["name"].casefold())
        if source is None or (source["ammo"], source["decay"]) != (item["ammo"], item["decay"]):
            orphans.append(item["name"])
    assert not orphans, f"a resync would drop or reprice: {orphans[:20]}"
