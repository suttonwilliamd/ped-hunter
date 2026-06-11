"""PyInstaller entrypoint for PED Hunter's Windows executable."""
from ped_hunter.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
