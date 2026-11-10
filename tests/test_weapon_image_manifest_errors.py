"""Broken optional image metadata must not break setup switching (headless)."""
import json
import time

import pytest

from ped_hunter.weapon_images import WeaponImageCache


@pytest.mark.parametrize("value", [None, [], 1, {}, {"schema_version": 1, "weapons": []}])
def test_broken_optional_manifest_yields_explicit_fallback(tmp_path, value):
    (tmp_path / "weapon_images.json").write_text(json.dumps(value), encoding="utf-8")
    cache = WeaponImageCache(tmp_path)
    try:
        cache.select(["Falcata"])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            results = cache.poll()
            if not cache.pending:
                break
            time.sleep(.002)
        assert not cache.pending
        assert results["Falcata"].status == "broken_asset"
        assert results["Falcata"].data is None
    finally:
        cache.close()
