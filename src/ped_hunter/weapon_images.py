"""Exact-identity, local-only HUD imagery; no network or Tk calls in workers."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import zlib

from .catalog import _default_catalog_root


MAX_IMAGE_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 256
MAX_CACHED_IMAGES = 32
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True, slots=True)
class WeaponImage:
    name: str
    status: str
    data: bytes | None = None
    width: int = 0
    height: int = 0
    sha256: str = ""


def exact_weapon_names(weapons: dict, aliases: dict[str, str]) -> dict[str, str]:
    """Canonical spelling or an explicit catalog alias, never substring search."""
    lookup: dict[str, str | None] = {}
    for name in weapons:
        key = name.casefold().strip()
        lookup[key] = name if key not in lookup or lookup[key] == name else None
    for alias, name in aliases.items():
        if name not in weapons:
            continue
        key = alias.casefold().strip()
        lookup[key] = name if key not in lookup or lookup[key] == name else None
    return {key: name for key, name in lookup.items() if name is not None}


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Bound decoded pixel storage and reject truncated/corrupt PNG containers."""
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(PNG_SIGNATURE):
        raise ValueError("Expected a small PNG thumbnail")
    offset = len(PNG_SIGNATURE)
    dimensions = None
    has_pixels = False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("Truncated PNG chunk")
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:end - 4]
        if zlib.crc32(kind + body) != int.from_bytes(data[end - 4:end], "big"):
            raise ValueError("PNG checksum mismatch")
        if dimensions is None:
            if kind != b"IHDR" or length != 13:
                raise ValueError("Missing PNG dimensions")
            dimensions = struct.unpack(">II", body[:8])
            if not all(0 < side <= MAX_IMAGE_SIDE for side in dimensions):
                raise ValueError("Thumbnail dimensions exceed the supported bound")
        elif kind == b"IHDR":
            raise ValueError("Repeated PNG header")
        if kind == b"IDAT":
            has_pixels = True
        if kind == b"IEND":
            if length or end != len(data) or not has_pixels:
                raise ValueError("Invalid PNG ending")
            return dimensions
        offset = end
    raise ValueError("Incomplete PNG")


def neutral_image(size: int) -> bytes:
    """Original transparent unavailable-image mark, never a substitute weapon."""
    size = max(8, min(64, int(size)))
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG scanline filter
        for x in range(size):
            nx, ny = x * 16 // size, y * 16 // size
            border = (nx in (2, 13) and 2 <= ny <= 13) or (ny in (2, 13) and 2 <= nx <= 13)
            question = (5 <= nx <= 9 and ny == 5) or (nx == 10 and 6 <= ny <= 7) or (7 <= nx <= 9 and ny == 8) or (nx == 7 and ny in (9, 11))
            rows.extend((148, 163, 184, 255 if border or question else 0))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class WeaponImageCache:
    """One local reader, at most four wanted identities and 32 cached results.

    The owner calls select/poll/close on its UI thread. Only immutable byte
    results cross the worker boundary; PhotoImage construction stays on Tk.
    Missing/blocked assets are cached too, avoiding repeated disk/network work.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _default_catalog_root()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weapon-images")
        self._entries: dict | None = None  # accessed only by the single reader
        self._wanted: tuple[str, ...] = ()
        self._pending: Future | None = None
        self._cache: OrderedDict[str, WeaponImage] = OrderedDict()
        self._closed = False

    def select(self, names: list[str | None]) -> None:
        if self._closed:
            return
        if len(names) > 4:
            raise ValueError("HUD imagery supports four slots")
        self._wanted = tuple(dict.fromkeys(name for name in names if name))
        for name in self._wanted:
            if name in self._cache:
                self._cache.move_to_end(name)
        self._schedule()

    @property
    def pending(self) -> bool:
        return not self._closed and any(name not in self._cache for name in self._wanted)

    def poll(self) -> dict[str, WeaponImage]:
        if self._closed:
            return {}
        if self._pending is not None and self._pending.done():
            results = self._pending.result()
            self._pending = None
            for result in results:
                self._cache[result.name] = result
                self._cache.move_to_end(result.name)
                while len(self._cache) > MAX_CACHED_IMAGES:
                    self._cache.popitem(last=False)
        self._schedule()
        # Never return obsolete completions as the currently selected weapon.
        return {name: self._cache.get(name) or WeaponImage(name, "pending") for name in self._wanted}

    def _schedule(self) -> None:
        if self._pending is not None:
            return
        missing = tuple(name for name in self._wanted if name not in self._cache)
        if missing:
            # Keep one finite batch, not an executor queue of cancelled stale
            # requests when the user switches repeatedly during a slow read.
            self._pending = self._executor.submit(self._read_batch, missing)

    def _read_batch(self, names: tuple[str, ...]) -> list[WeaponImage]:
        return [self._read(name) for name in names]

    def _read(self, name: str) -> WeaponImage:
        try:
            if self._entries is None:
                path = self.root / "weapon_images.json"
                with path.open("rb") as source:
                    raw = source.read(MAX_MANIFEST_BYTES + 1)
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise ValueError("Image manifest exceeds supported size")
                manifest = json.loads(raw)
                if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                        or not isinstance(manifest.get("weapons"), dict)):
                    raise ValueError("Unsupported image manifest")
                self._entries = manifest["weapons"]
            entry = self._entries.get(name)
            if not isinstance(entry, dict):
                return WeaponImage(name, "unknown_weapon")
            if entry.get("status") != "available":
                return WeaponImage(name, entry.get("status", "unavailable"))
            identity = entry.get("identity")
            if entry.get("identity_status") != "exact" or not isinstance(identity, dict) or identity.get("name") != name:
                return WeaponImage(name, "identity_unresolved")
            asset, reuse = entry.get("asset"), entry.get("reuse", {})
            if not isinstance(reuse, dict):
                return WeaponImage(name, "broken_asset")
            if not reuse.get("basis") or not reuse.get("evidence_url") or reuse.get("status") != "approved":
                return WeaponImage(name, "reuse_unresolved")
            if not isinstance(asset, dict):
                return WeaponImage(name, "broken_asset")
            relative = Path(asset["path"])
            if relative.is_absolute() or relative.parts[:1] != ("weapon_images",) or ".." in relative.parts:
                return WeaponImage(name, "broken_asset")
            path = (self.root / relative).resolve()
            if not path.is_relative_to((self.root / "weapon_images").resolve()):
                return WeaponImage(name, "broken_asset")
            with path.open("rb") as source:
                data = source.read(MAX_IMAGE_BYTES + 1)
            sha = hashlib.sha256(data).hexdigest()
            if sha != asset["sha256"]:
                return WeaponImage(name, "broken_asset")
            width, height = png_dimensions(data)
            if [width, height] != [asset["width"], asset["height"]]:
                return WeaponImage(name, "broken_asset")
            return WeaponImage(name, "ready", data, width, height, sha)
        except (OSError, ValueError, KeyError, TypeError):
            return WeaponImage(name, "broken_asset")

    def close(self) -> None:
        self._closed = True
        self._wanted = ()
        self._pending = None
        self._cache.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)
