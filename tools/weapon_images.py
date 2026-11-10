"""Offline, exact-name image audit. No scraping, downloads, GUI or Store access.

Supply saved public API identity responses and actual image-probe evidence.
Artwork is admitted only with an explicit reviewed identity/reuse record; an
accessible URL or a matching-looking base weapon is not such evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ped_hunter.weapon_images import MAX_IMAGE_BYTES, png_dimensions


NEXUS_TERMS = "https://entropianexus.com/legal/terms"


def identity_record(row: dict, kind: str) -> dict:
    return {"source": "entropia_nexus", "entity_type": kind,
            "id": row["Id"], "item_id": row["ItemId"], "name": row["Name"]}


def audit(catalog_path: Path, snapshots: list[dict], probes: dict, reviewed: dict,
          *, observed_at: str, revision: str) -> tuple[dict, dict]:
    catalog_bytes = catalog_path.read_bytes()
    rows = json.loads(catalog_bytes)["items"]
    names = [row["name"] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate exact catalog identities must be resolved before auditing")
    exact, folded = defaultdict(list), defaultdict(list)
    for snapshot in snapshots:
        kind = snapshot["entity_type"]
        for row in snapshot["records"]:
            identity = identity_record(row, kind)
            exact[kind, row["Name"]].append(identity)
            folded[kind, " ".join(row["Name"].split()).casefold()].append(identity)
    entries = {}
    asset_users = defaultdict(list)
    for row in rows:
        name = row["name"]
        kind = "finder" if row.get("category") == "Mining Finder" else "weapon"
        matches = exact[kind, name]
        identity = matches[0] if len(matches) == 1 else None
        candidates = matches or folded[kind, " ".join(name.split()).casefold()]
        entry = {
            "identity_status": "exact" if identity else "ambiguous" if candidates else "unmatched",
            "identity": identity,
            "candidates": candidates if not identity else [],
            "source_item_url": None,
            "source_item_url_basis": "exact_api_identity_route" if identity else None,
            "candidate_image_url": None,
            "original_image_url": None,
            "original_sha256": None,
            "identity_evidence": None,
            "processing": None,
            "attribution": None,
            "retrieved_at": None,
            "availability": "not_checked",
            "status": "not_retrieved" if identity else "identity_unresolved",
            "reuse": {"status": "unresolved", "basis": None, "evidence_url": NEXUS_TERMS},
            "asset": None,
        }
        if identity:
            section = "weapons" if kind == "weapon" else "tools/finders"
            encoded = quote(identity["name"].replace(" ", "~"), safe="~")
            entry["source_item_url"] = f"https://entropianexus.com/items/{section}/{encoded}"
            entry["candidate_image_url"] = f"https://entropianexus.com/api/image/{kind}/{identity['id']}"
            probe = probes.get(entry["candidate_image_url"])
            if probe:
                if not probe.get("observed_at") or probe.get("http_status") != 204:
                    raise ValueError("Only actual timestamped HTTP 204 absence evidence is supported")
                entry["availability"] = "missing_at_source"
                entry["status"] = "missing"
                entry["availability_checked_at"] = probe["observed_at"]
        previous = reviewed.get(name)
        if previous:
            # A catalog rename, variant change or source-ID change is not approval
            # to carry a previously reviewed image onto a different identity.
            if not identity or previous.get("identity") != identity or previous.get("catalog_name") != name:
                raise ValueError(f"Reviewed image identity changed: {name}")
            reuse, asset = previous.get("reuse", {}), previous.get("asset", {})
            required = ("source_item_url", "original_image_url", "original_sha256",
                        "retrieved_at", "identity_evidence", "processing", "attribution")
            if (not all(previous.get(key) for key in required) or reuse.get("status") != "approved"
                    or not reuse.get("basis") or not reuse.get("evidence_url")):
                raise ValueError(f"Incomplete image provenance/reuse evidence: {name}")
            entry.update({key: previous[key] for key in required})
            entry["source_item_url_basis"] = "reviewed_original_image_publication"
            entry["reuse"] = reuse
            if previous.get("source_image_record"):
                entry["source_image_record"] = previous["source_image_record"]
            if previous.get("status") == "unusable":
                if not previous.get("unusable_reason") or previous.get("asset") is not None:
                    raise ValueError(f"Unusable art must retain its reason and no usable asset: {name}")
                entry.update(status="unusable", availability="retrieved_unusable",
                             unusable_reason=previous["unusable_reason"])
                entries[name] = entry
                continue
            if previous.get("status") != "available":
                raise ValueError(f"Unsupported reviewed image status: {name}")
            relative = Path(asset.get("path", ""))
            root = (catalog_path.parent / "weapon_images").resolve()
            path = (catalog_path.parent / relative).resolve()
            if relative.is_absolute() or relative.parts[:1] != ("weapon_images",) or not path.is_relative_to(root):
                raise ValueError(f"Image must be a local catalog thumbnail: {name}")
            with path.open("rb") as source:
                data = source.read(MAX_IMAGE_BYTES + 1)
            width, height = png_dimensions(data)
            sha = hashlib.sha256(data).hexdigest()
            if asset != {"path": asset["path"], "sha256": sha, "width": width, "height": height}:
                raise ValueError(f"Image bytes/dimensions do not match their provenance: {name}")
            if previous.get("shared_image_evidence"):
                entry["shared_image_evidence"] = previous["shared_image_evidence"]
            entry.update(reuse=reuse, asset=asset, status="available", availability="retrieved")
            asset_users[asset["path"]].append(name)
        entries[name] = entry
    for path, users in asset_users.items():
        if len(users) > 1 and any(not reviewed[name].get("shared_image_evidence") for name in users):
            raise ValueError(f"Shared variant imagery requires source evidence for each identity: {path}")
    statuses = Counter(entry["status"] for entry in entries.values())
    identities = Counter(entry["identity_status"] for entry in entries.values())
    manifest = {
        "schema_version": 1,
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "catalog_revision": revision,
        "observed_at": observed_at,
        "sources": [{key: value for key, value in snapshot.items() if key != "records"} for snapshot in snapshots],
        "rights_and_research": "weapon_image_sources.json",
        "coverage": {"catalog_entries": len(names), "accounted_for": len(entries),
                     "identity": dict(sorted(identities.items())), "images": dict(sorted(statuses.items())),
                     "complete_image_coverage": statuses["available"] == len(names) and bool(names)},
        "weapons": entries,
    }
    inventory = {"catalog_sha256": manifest["catalog_sha256"], "coverage": manifest["coverage"],
                 "by_status": {status: sorted(name for name, entry in entries.items() if entry["status"] == status)
                               for status in sorted(statuses)}}
    return manifest, inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/weapons.json"))
    parser.add_argument("--snapshots", type=Path, required=True, help="Saved identity records and source/hash/retrieval metadata")
    parser.add_argument("--probes", type=Path, required=True, help="Actual timestamped HTTP 204 evidence, or an empty object")
    parser.add_argument("--reviewed", type=Path, default=Path("data/catalog/weapon_image_reviews.json"),
                        help="Exact-name reviewed art/provenance records; no inferred approvals")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/catalog/weapon_images.json"))
    parser.add_argument("--inventory", type=Path, default=Path("data/catalog/weapon_image_inventory.json"))
    args = parser.parse_args(argv)
    try:
        manifest, inventory = audit(
            args.catalog, json.loads(args.snapshots.read_text(encoding="utf-8")),
            json.loads(args.probes.read_text(encoding="utf-8")),
            json.loads(args.reviewed.read_text(encoding="utf-8")) if args.reviewed else {},
            observed_at=datetime.now(timezone.utc).isoformat(), revision=args.revision)
        args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        args.inventory.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Image audit failed: {error}\n")
    print(json.dumps(manifest["coverage"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
