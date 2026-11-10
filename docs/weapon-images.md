# Weapon imagery: identity audit and HUD loading

## Current coverage, not a complete artwork claim

The audit covers all **2,899 unique entries** in `data/catalog/weapons.json`,
including its eleven mining finders. Catalog SHA256:
`914fd48ca3bae8ac8dc5c7baafb6e56666cd9efe07dc706169cade7232761160`.
The catalog is unchanged from `d04cd1d5d1605b9dd61d7f906161f644b2e14ae0`.

| Identity result against saved public Nexus indexes | Entries |
| --- | ---: |
| Exact name and entity type | 2,887 |
| Whitespace/case candidate only; not automatically approved | 5 |
| No exact identity or normalization candidate | 7 |

| Artwork status | Entries |
| --- | ---: |
| Exact weapon images bundled with source reuse evidence | 1 |
| Retrieved image unusable because the model is obscured | 1 |
| Exact image endpoint checked and returned HTTP 204 | 3 |
| Image not retrieved; availability/reuse not established | 2,882 |
| Identity unresolved; no substitute selected | 12 |

These are separate identity/artwork dimensions, not additive coverage totals.
Every entry is accounted for; **recognizable image coverage is not complete**.
Most image endpoints have not been checked. Do not label them all missing.
Falcata, SET Combat Blade (L), and Basic Mining Laser, Type M (L) returned
HTTP 204 from their exact Nexus image endpoints during bounded research.
A historical **Sollomate Opalo** image was extracted from the explicitly
CC BY-SA 4.0 Entropia Universe Guide (2017 Edition, page 14). Its original
screenshot names this exact item. The 64×64 crop preserves the complete weapon
without reconstruction; its current in-game appearance is unverified. A
Bukin's Blade screenshot on page 16 is obscured by text/annotations and is
recorded **unusable**, not turned into a guessed clean image. See the bundled
[attribution and change notice](../data/catalog/weapon_images/ATTRIBUTION.md).

`weapon_images.json` maps every exact catalog name to source identity, item
page, candidate versus actually retrieved URL, availability, provenance,
reuse state and local asset/hash. `weapon_image_inventory.json` lists the full
unresolved/missing/not-retrieved inventory. `weapon_image_sources.json`
records source restrictions and research limitations. The catalog hash and
coverage test expose newly added or changed catalog entries rather than
silently excluding them from the audit.

## Sources and reuse limits

- [Entropia Universe Guide, 2017 Edition](https://www.entropiamuseum.com/guides/2014/08/guide2014081.pdf):
  explicit CC BY-SA 4.0 content notice on page 1 and reuse/permission statement
  on page 8. Exact Opalo title/model checked in page 14's original JPEG object
  176; one crop is bundled with attribution and ShareAlike terms. Page 16's
  annotated Bukin's Blade is not usable. Raw PDF, extracted JPEG and derived
  PNG hashes, original dimensions, crop/resampling and author credits are
  retained. A windowless owned System.Drawing memory-bitmap conversion was
  used; no new image dependency, viewer or game operation.
- [Nexus public weapons API](https://api.entropianexus.com/weapons) and
  [finders API](https://api.entropianexus.com/finders): one saved bulk identity
  response each, not thousands of item-page requests. The weapon response had
  3,150 records. Neither response supplies an artwork license.
- [Nexus terms](https://entropianexus.com/legal/terms) identify MindArk ownership
  and Nexus's informational fair-use basis. Contributor permissions run to
  Nexus; no general visitor artwork-redistribution grant was found. Its
  [source-available repository license](https://github.com/mdragosv/Entropia-Nexus/blob/371b7c99d192053776aa9e6e209b27f0a979d2c0/LICENSE)
  is not an artwork grant. No Nexus code/assets were copied into this project.
- [Central Falcata](https://www.entropiacentral.com/wiki/weapons/falcata)
  explicitly reports its image missing. Its social-card logo is not a weapon.
  [Central terms](https://www.entropiacentral.com/terms-of-service) prohibit
  automated extraction without written permission. No bulk crawl was made.
- [Life Falcata](https://www.entropialife.com/about/Guides/Trading.aspx?M=showItemInfo&itemID=2246)
  links `https://www.entropialife.com/images/dynamicImages/auctionItems/2246.jpg`.
  Its pixels/reuse were not verified and it was not downloaded.
  [Life legal terms](https://www.entropialife.com/about/legal.aspx) require
  permission for software cataloguing/distributing collected data.
- [MindArk's notice](https://account.entropiauniverse.com/legal/legal-notice)
  reserves ungranted rights and describes personal/non-commercial informational
  website-content use, not a general third-party artwork redistribution grant.
- EntropiaWiki HTTP/HTTPS About requests and a known alternate-host item page
  failed with `ECONNREFUSED`. No security/TLS workaround was used. The original
  LootNanny repository inventory contains application screenshots/icons, not
  an exact-weapon artwork collection.

Public accessibility, a generic logo, a similar base weapon, a community site's
own fair-use assertion, and an MIT license on application code are **not**
automatic approval to redistribute all item art. This research does not decide
whether a particular informational use is legally fair use. Appropriate
source/owner permission or another reviewed reuse basis and exact art are
still needed. No permission requests were sent or legal terms accepted on the
user's behalf. The original neutral question-mark image is PED Hunter artwork,
not a substitute weapon illustration. Future third-party art retains its
own recorded attribution/license requirements; the application's MIT license
does not override them.

## Reproduce or update the audit without repeated downloads

`tools/weapon_images.py` is **offline**. It does not access Store, a chat log,
a browser, or the network. Supply saved responses rather than repeatedly
fetching unchanged sources:

```powershell
.\.venv\Scripts\python.exe tools\weapon_images.py `
  --snapshots path\to\identity-snapshots.json `
  --probes path\to\image-probes.json `
  --revision <catalog-source-revision>
```

Each snapshot contains `entity_type` (`weapon` or `finder`), source URL,
retrieval/provenance/hash metadata and `records` with `Id`, `ItemId`, `Name`.
Hashes of reader-rendered JSON are labeled as such, not wire-byte hashes.
Probe evidence is keyed by the exact candidate URL and records actual
`http_status: 204` and observation date/time; `{}` means no checked absence,
not proof that every image is missing. Do not bulk-query a restricted source
or convert an error/timeout into missing-image evidence.

The default reviewed records are `data/catalog/weapon_image_reviews.json`;
preserve them when regenerating rather than losing an already approved image.
To admit additional eligible art, update that file or explicitly supply
`--reviewed reviewed-art.json`, keyed by the **exact catalog name**.
Each available record must include:

- `catalog_name` and the exact unchanged `identity` from the manifest;
- actual `source_item_url`, `original_image_url`, `original_sha256`,
  `retrieved_at`, `identity_evidence`, `processing` and `attribution`;
- `reuse: {status: "approved", basis: "…", evidence_url: "…"}` describing
  the actual reviewed basis/conditions, not an inferred source-wide grant;
- `asset: {path: "weapon_images/…png", sha256: "…", width: …, height: …}`.

Use `status: "unusable"`, `asset: null`, the actual original-source provenance
and an explicit `unusable_reason` for inspected but unsuitable art. Do not turn
obscured model details into a fictional clean weapon. Reviewed source changes
must retain their record and be revalidated, not silently inherited.

Keep original retrieval/processing evidence alongside the review. Prefer a
small square transparent PNG thumbnail with sufficient source resolution;
local thumbnails must be at most 64 KiB and 256×256. No image converter or new
runtime dependency is installed by this command. Keep any required notices
and ShareAlike conditions with the artwork. When multiple variants use one
local asset, **each** reviewed identity needs `shared_image_evidence` from the
sources; equal-looking names or files alone are insufficient. The audit checks
actual local bytes/hash/dimensions and refuses stale identity approval.
Unreviewed/candidate matches remain neutral. This process does not authorize
access to restricted sources or invent permission.

## Runtime behavior

The four persisted setup slots keep their IDs, order, activation commands,
active colors/check, disabled empty slots and accounting behavior. Artwork is
resolved from the saved **weapon**, not the setup's arbitrary display name.
Only exact catalog identities and explicit aliases are used—never catalog
substring search or Type A/Type M substitution. Renaming a setup does not
change its weapon image; changing its weapon requests the new identity.

The HUD requests **local** approved PNGs through one background reader with
one finite batch (at most four current identities), 32 cached byte results and
32 cached Tk images. Rapid changes do not queue unbounded cancelled jobs.
Labels/active state update immediately; obsolete byte completions cannot pick
a new active setup or replace a current slot's identity. Tk image construction
stays on the owner thread. Reopening the HUD creates a new cache; destroying
it cancels its poll and closes owned worker work. No remote image request,
SQLite write, source-log read, or cost update occurs in image loading.

Images/neutral marks occupy a compact lane inside the existing buttons. The
slot number/active check and shortened setup name use separate text lines in
the existing row height; the full setup name remains in the hover/focus hint.
Empty slots have no misleading image/action. Missing, unreviewed, ambiguous,
rights-unresolved and broken art displays the original neutral mark plus the
saved label and an unavailable-image hint.

## Verification scope and outstanding acceptance

`tests/test_weapon_images.py` uses **original synthetic PNG fixtures**, not
claimed weapon pictures. It covers exact variants/aliases, all four requested
identities, unsupported variants, missing/broken/unapproved assets, rapid
stale changes under a blocked reader, finite work/cache storage, empty/repeated
selection, shutdown, actual file/hash/dimensions, audit identity/reuse/shared
asset guards and complete catalog inventory. `catalog-loadouts` and
`desktop-ui` include this file; full discovery is unchanged.

Actual compact native rendering, readability, active contrast, resizing and
representative Windows DPI screenshots **remain pending** under PED-014's
background/headless-only instruction. No live application/installation/session,
HUD, window, settings, game or system scaling was changed. Headless checks and
synthetic art are not native/recognizable-art acceptance. Installation and a
session-preserving cutover are also pending; parked PED-013 delivery must not
be silently included. Complete actual artwork coverage remains limited by the
explicit inventory and unresolved source/reuse evidence above.
