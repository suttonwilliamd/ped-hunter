"""Headless image identity, provenance and asynchronous resource contracts.

Synthetic original PNGs here are test fixtures, not catalog weapon artwork.
Native button/font/contrast acceptance is a separate, explicitly pending gate.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy
import threading
import time

import pytest

from ped_hunter.catalog import Catalog
from ped_hunter.weapon_images import (
    MAX_CACHED_IMAGES, WeaponImageCache, exact_weapon_names, neutral_image, png_dimensions,
)


ROOT = Path(__file__).resolve().parents[1]


def asset_entry(root, name, size=24):
    data = neutral_image(size)
    sha = hashlib.sha256(data).hexdigest()
    path = root / "weapon_images" / f"{sha}.png"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    return {
        "catalog_name": name,
        "identity_status": "exact",
        "identity": {"source": "entropia_nexus", "entity_type": "weapon", "id": 1, "item_id": 2000001, "name": name},
        "status": "available",
        "source_item_url": "https://example.invalid/exact-test-item",
        "original_image_url": "https://example.invalid/original-test-art.png",
        "original_sha256": sha,
        "processing": "Original synthetic PNG fixture; no third-party artwork",
        "attribution": "PED Hunter original test fixture",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "identity_evidence": "Exact synthetic fixture identity, not a real item-image claim",
        "reuse": {"status": "approved", "basis": "Original synthetic test artwork", "evidence_url": "https://example.invalid/test-license"},
        "asset": {"path": f"weapon_images/{sha}.png", "sha256": sha, "width": size, "height": size},
    }


def manifest(root, entries):
    path = root / "weapon_images.json"
    path.write_text(json.dumps({"schema_version": 1, "weapons": entries}), encoding="utf-8")
    return path


def completed(cache, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        results = cache.poll()
        if not cache.pending:
            return results
        time.sleep(.002)
    pytest.fail("Local image worker did not complete")


def test_exact_catalog_names_and_explicit_aliases_never_use_substrings():
    catalog = Catalog.load()
    names = exact_weapon_names(catalog.weapons, catalog.aliases)
    assert names["frontier rifle"] == "Frontier Hunting Rifle"
    assert names["basic mining laser, type m (l)"] == "Basic Mining Laser, Type M (L)"
    # If Type A is added later, it must remain itself, never borrow Type M.
    assert names.get("basic mining laser, type a (l)") in (None, "Basic Mining Laser, Type A (L)")
    assert "frontier" not in names
    assert "basic mining laser" not in names
    assert "falcata, unknown variant" not in names
    assert "same" not in exact_weapon_names({"Same": object(), "SAME": object()}, {})


def test_four_exact_images_and_unknown_variant_are_not_interchanged(tmp_path):
    names = ["Falcata", "Falcata (L)", "Basic Mining Laser, Type M (L)", "Basic Mining Laser, Type A (L)"]
    entries = {name: asset_entry(tmp_path, name, 16 + index) for index, name in enumerate(names)}
    manifest(tmp_path, entries)
    cache = WeaponImageCache(tmp_path)
    try:
        cache.select(names)
        results = completed(cache)
        assert list(results) == names
        for name in names:
            assert results[name].status == "ready"
            assert results[name].sha256 == entries[name]["asset"]["sha256"]
            assert png_dimensions(results[name].data) == (entries[name]["asset"]["width"],) * 2
        cache.select(["Falcata, Improved", names[-1]])
        results = completed(cache)
        assert results["Falcata, Improved"].status == "unknown_weapon"
        assert results[names[-1]].sha256 == entries[names[-1]]["asset"]["sha256"]
        assert names[0] not in results
    finally:
        cache.close()


@pytest.mark.parametrize("defect", ["missing", "hash", "png", "dimensions", "unlicensed", "traversal", "wrong_variant"])
def test_unusable_artwork_is_an_explicit_neutral_fallback_not_a_different_weapon(tmp_path, defect):
    entry = asset_entry(tmp_path, "Falcata")
    asset_path = tmp_path / entry["asset"]["path"]
    if defect == "missing":
        asset_path.unlink()
    elif defect == "hash":
        entry["asset"]["sha256"] = "0" * 64
    elif defect == "png":
        data = b"not a PNG"
        asset_path.write_bytes(data)
        entry["asset"]["sha256"] = hashlib.sha256(data).hexdigest()
    elif defect == "dimensions":
        entry["asset"]["width"] += 1
    elif defect == "unlicensed":
        entry["reuse"]["status"] = "unresolved"
    elif defect == "wrong_variant":
        entry["identity"]["name"] = "Falcata (L)"
    else:
        entry["asset"]["path"] = "weapon_images/../../outside.png"
    manifest(tmp_path, {"Falcata": entry})
    cache = WeaponImageCache(tmp_path)
    try:
        cache.select(["Falcata"])
        result = completed(cache)["Falcata"]
        assert result.data is None
        expected = {"unlicensed": "reuse_unresolved", "wrong_variant": "identity_unresolved"}
        assert result.status == expected.get(defect, "broken_asset")
    finally:
        cache.close()


def test_slow_read_and_rapid_changes_remain_nonblocking_bounded_and_latest_only(tmp_path, monkeypatch):
    manifest(tmp_path, {"Latest": asset_entry(tmp_path, "Latest")})
    cache = WeaponImageCache(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = cache._read

    def delayed(name):
        entered.set()
        assert release.wait(3)
        return original(name)

    monkeypatch.setattr(cache, "_read", delayed)
    try:
        cache.select(["Old", "Old2", "Old3", "Old4"])
        assert entered.wait(2)
        original_batch = cache._pending
        for index in range(200):
            cache.select([f"Changed {index}", "Latest", None, None])
            assert cache._pending is original_batch
            assert len(cache._wanted) == 2
        # The blocked reader has not completed, yet every selection returned.
        assert not original_batch.done()
        cache.select(["Latest"])
        assert set(cache.poll()) == {"Latest"}
        release.set()
        results = completed(cache)
        assert list(results) == ["Latest"] and results["Latest"].status == "ready"
        assert len(cache._cache) <= MAX_CACHED_IMAGES
    finally:
        release.set()
        cache.close()


def test_repeated_and_empty_selection_cache_negative_results_and_close_cleanly(tmp_path):
    manifest(tmp_path, {"Missing": {"status": "missing"}})
    cache = WeaponImageCache(tmp_path)
    cache.select(["Missing"] * 4)
    first = completed(cache)["Missing"]
    (tmp_path / "weapon_images.json").unlink()
    cache.select(["Missing"] * 4)
    assert cache.poll()["Missing"] is first and first.status == "missing"
    assert cache._pending is None
    cache.select([])
    assert cache.poll() == {} and not cache.pending
    cache.close()
    cache.select(["Missing"])
    assert cache.poll() == {} and not cache.pending


def test_loaded_thumbnail_memory_is_bounded_across_many_weapon_changes(tmp_path):
    manifest(tmp_path, {})
    cache = WeaponImageCache(tmp_path)
    try:
        for index in range(MAX_CACHED_IMAGES + 8):
            cache.select([f"Unavailable {index}"])
            completed(cache)
            assert len(cache._cache) <= MAX_CACHED_IMAGES
        assert "Unavailable 0" not in cache._cache
        assert "Unavailable 39" in cache._cache
    finally:
        cache.close()


@pytest.mark.parametrize("size", [8, 16, 24, 32, 64])
def test_original_neutral_mark_has_bounded_valid_transparent_png_storage(size):
    import zlib
    data = neutral_image(size)
    assert png_dimensions(data) == (size, size)
    start = data.index(b"IDAT") + 4
    length = int.from_bytes(data[start - 8:start - 4], "big")
    pixels = zlib.decompress(data[start:start + length])
    assert len(pixels) == size * (size * 4 + 1)
    alphas = {pixels[y * (size * 4 + 1) + 1 + x * 4 + 3] for y in range(size) for x in range(size)}
    assert alphas == {0, 255}


def audit_tool():
    return runpy.run_path(str(ROOT / "tools/weapon_images.py"))["audit"]


def test_audit_accounts_for_every_variant_and_does_not_approve_normalization_or_urls(tmp_path):
    catalog = tmp_path / "weapons.json"
    names = ["Exact (L)", "Exact", "Spaced  Name (L)", "Unknown", "Finder X"]
    catalog.write_text(json.dumps({"items": [{"name": name, "category": "Mining Finder" if name == "Finder X" else "Pistol"} for name in names]}))
    snapshots = [{"entity_type": "weapon", "source_url": "https://example.invalid/weapons", "records": [
        {"Name": name, "Id": i, "ItemId": 2000000 + i} for i, name in enumerate(["Exact (L)", "Exact", "Spaced Name (L)"])]},
        {"entity_type": "finder", "records": [{"Name": "Finder X", "Id": 1, "ItemId": 4500001}]}]
    manifest_data, inventory = audit_tool()(catalog, snapshots, {}, {}, observed_at="2026-01-01", revision="test")
    entries = manifest_data["weapons"]
    assert set(entries) == set(names)
    assert entries["Exact"]["identity"]["id"] != entries["Exact (L)"]["identity"]["id"]
    assert entries["Spaced  Name (L)"]["status"] == "identity_unresolved"
    assert entries["Spaced  Name (L)"]["identity"] is None
    assert entries["Unknown"]["candidate_image_url"] is None
    assert entries["Finder X"]["identity"]["entity_type"] == "finder"
    assert entries["Exact"]["candidate_image_url"] and entries["Exact"]["original_image_url"] is None
    assert not manifest_data["coverage"]["complete_image_coverage"]
    assert set(sum(inventory["by_status"].values(), [])) == set(names)
    assert manifest_data["catalog_sha256"] == hashlib.sha256(catalog.read_bytes()).hexdigest()


def test_reviewed_art_requires_exact_unchanged_identity_and_reuse_and_shared_evidence(tmp_path):
    names = ["Falcata", "Falcata (L)"]
    catalog = tmp_path / "weapons.json"
    catalog.write_text(json.dumps({"items": [{"name": name} for name in names]}))
    snapshot = [{"entity_type": "weapon", "records": [{"Name": name, "Id": i + 1, "ItemId": 2000001 + i} for i, name in enumerate(names)]}]
    reviewed = {name: asset_entry(tmp_path, name) for name in names}
    reviewed[names[1]]["identity"].update(id=2, item_id=2000002)
    audit = audit_tool()
    with pytest.raises(ValueError, match="Shared variant"):
        audit(catalog, snapshot, {}, reviewed, observed_at="now", revision="test")
    for entry in reviewed.values():
        entry["shared_image_evidence"] = "Both exact synthetic source records explicitly supply this identical original test image"
    result, _ = audit(catalog, snapshot, {}, reviewed, observed_at="now", revision="test")
    assert result["coverage"]["complete_image_coverage"]
    changed = deepcopy(snapshot)
    changed[0]["records"][0]["Id"] = 999
    with pytest.raises(ValueError, match="identity changed"):
        audit(catalog, changed, {}, reviewed, observed_at="now", revision="test")
    reviewed[names[0]]["reuse"]["status"] = "unresolved"
    with pytest.raises(ValueError, match="provenance/reuse"):
        audit(catalog, snapshot, {}, reviewed, observed_at="now", revision="test")


def test_generated_current_manifest_has_no_silent_exclusions_or_false_complete_claim():
    data = json.loads((ROOT / "data/catalog/weapon_images.json").read_text(encoding="utf-8"))
    catalog = ROOT / "data/catalog/weapons.json"
    names = {row["name"] for row in json.loads(catalog.read_text(encoding="utf-8"))["items"]}
    assert data["catalog_sha256"] == hashlib.sha256(catalog.read_bytes()).hexdigest(), "Regenerate the image audit after a catalog update"
    assert set(data["weapons"]) == names
    counts = {status: sum(entry["status"] == status for entry in data["weapons"].values())
              for status in data["coverage"]["images"]}
    assert counts == data["coverage"]["images"]
    assert data["coverage"]["complete_image_coverage"] == all(entry["status"] == "available" for entry in data["weapons"].values())
    for entry in data["weapons"].values():
        if entry["status"] == "available":
            assert entry["reuse"]["status"] == "approved" and entry["asset"] and entry["original_image_url"]
    available = [name for name, entry in data["weapons"].items() if entry["status"] == "available"]
    cache = WeaponImageCache(ROOT / "data/catalog")
    try:
        for start in range(0, len(available), 4):
            cache.select(available[start:start + 4])
            results = completed(cache)
            for name, result in results.items():
                assert result.status == "ready"
                assert result.sha256 == data["weapons"][name]["asset"]["sha256"]
    finally:
        cache.close()


def test_existing_frozen_catalog_location_resolves_actual_local_thumbnail(tmp_path, monkeypatch):
    import sys
    root = tmp_path / "data" / "catalog"
    root.mkdir(parents=True)
    entry = asset_entry(root, "Falcata")
    manifest(root, {"Falcata": entry})
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    cache = WeaponImageCache()
    try:
        cache.select(["Falcata"])
        result = completed(cache)["Falcata"]
        assert result.status == "ready" and result.sha256 == entry["asset"]["sha256"]
        assert result.width == result.height == 24
    finally:
        cache.close()


def test_inspected_but_obscured_art_remains_explicitly_unusable(tmp_path):
    name = "Falcata"
    catalog = tmp_path / "weapons.json"
    catalog.write_text(json.dumps({"items": [{"name": name}]}))
    snapshot = [{"entity_type": "weapon", "records": [{"Name": name, "Id": 1, "ItemId": 2000001}]}]
    reviewed = asset_entry(tmp_path, name)
    reviewed.update(status="unusable", asset=None, unusable_reason="Source annotations obscure the complete model")
    data, inventory = audit_tool()(catalog, snapshot, {}, {name: reviewed}, observed_at="now", revision="test")
    assert data["weapons"][name]["status"] == "unusable"
    assert data["weapons"][name]["asset"] is None
    assert inventory["by_status"]["unusable"] == [name]
    assert not data["coverage"]["complete_image_coverage"]
    (tmp_path / "weapon_images.json").write_text(json.dumps(data))
    cache = WeaponImageCache(tmp_path)
    try:
        cache.select([name])
        result = completed(cache)[name]
        assert result.status == "unusable" and result.data is None
    finally:
        cache.close()
