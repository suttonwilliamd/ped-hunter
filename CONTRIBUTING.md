# Contributing to PED Hunter

Thanks for helping improve PED Hunter. Contributions should keep the project free, local-first, and safe for people who use it with real Entropia Universe chat data.

## Before you start

- Search [existing issues](https://github.com/suttonwilliamd/ped-hunter/issues) before opening a new one.
- Use the appropriate form for a [bug](https://github.com/suttonwilliamd/ped-hunter/issues/new?template=bug_report.yml), [feature request](https://github.com/suttonwilliamd/ped-hunter/issues/new?template=feature_request.yml), or [catalog correction](https://github.com/suttonwilliamd/ped-hunter/issues/new?template=catalog_correction.yml).
- For a suspected vulnerability, do not open a public issue. Follow [SECURITY.md](SECURITY.md).
- For a substantial or user-visible change, open an issue first so scope and behavior can be agreed before implementation.

## Development setup

PED Hunter requires Python 3.11 or newer. From a clone of the repository:

```bash
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux
source .venv/bin/activate
```

Install the project and development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Launch the desktop application with:

```bash
python -m ped_hunter gui
```

## Tests

Run the complete suite on Windows:

```powershell
python -m pytest
```

The desktop tests need a display. On Debian or Ubuntu Linux, install Xvfb and run:

```bash
sudo apt-get update
sudo apt-get install --yes xvfb
xvfb-run --auto-servernum python -m pytest
```

Add or update tests for behavioral changes. Before submitting a pull request, run the full suite on your platform and report the exact command and result in the PR.

## Issue guidance

A useful bug report includes the PED Hunter version, operating system, expected behavior, actual behavior, and the smallest reproducible sequence. Use synthetic or thoroughly sanitized examples. Do not paste an entire chat log or attach a production SQLite database.

Feature requests should explain the user problem and desired outcome rather than prescribing a large implementation. Mention any effect on local storage, network access, or privacy.

### Catalog corrections require evidence

Catalog data is community-derived and can become stale. A correction must identify the exact item and field, show the current and proposed values, and cite evidence that maintainers can review. Prefer stable public source URLs and include the date checked. If public documentation is unavailable, a tightly cropped and sanitized in-game screenshot may be used, but it must not expose player names, chat, account details, or other personal data.

Corrections without reproducible evidence may be held until they can be verified. Update normalized files under `data/catalog/` consistently, and add or adjust catalog tests when appropriate.

## Privacy rules

Repository history, issues, pull requests, and CI logs may be public. Never commit, paste, or attach:

- real or unsanitized chat logs or chat excerpts;
- production PED Hunter SQLite databases, database backups, or crash logs;
- player names, account identifiers, private messages, email addresses, or other personal data;
- secrets, tokens, credentials, or local machine paths that reveal personal information.

Tests and documentation must use invented names and synthetic data. Minimize fixtures to only the lines needed to exercise the behavior. If a privacy-sensitive sample is necessary to diagnose a problem, ask a maintainer for a safe private route rather than publishing it.

Any new network operation, telemetry, external service, or change to stored user data must be called out explicitly and documented in [PRIVACY.md](PRIVACY.md).

## Pull request scope

- Keep each PR focused on one issue or cohesive change.
- Avoid drive-by refactors, generated artifacts, and unrelated formatting changes.
- Do not mix catalog changes with application behavior unless they are inseparable.
- Preserve backward compatibility for existing local databases when practical, and document migrations or data-format changes.
- Update README, privacy, security, or user-facing help when behavior changes.
- Link the relevant issue and describe how the change was tested.

## Version synchronization

The project version is duplicated intentionally for packaging. If a release change updates the version, keep these values synchronized:

- `pyproject.toml` — `[project].version`
- `src/ped_hunter/__init__.py` — `__version__`
- `installer/PED-Hunter.iss` — `MyAppVersion`

`tests/test_version_consistency.py` enforces those three values. Also update release-facing documentation and asset names, including any versioned Windows download link in `README.md`, as part of the release process.

## License

By contributing, you agree that your contribution will be licensed under the repository's [MIT License](LICENSE).
