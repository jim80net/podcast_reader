from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile

EXPECTED = {
    "podcast_reader_premium/static/admin.css",
    "podcast_reader_premium/templates/ads.html",
    "podcast_reader_premium/templates/account.html",
    "podcast_reader_premium/templates/audit.html",
    "podcast_reader_premium/templates/base.html",
    "podcast_reader_premium/templates/device.html",
    "podcast_reader_premium/templates/flags.html",
    "podcast_reader_premium/templates/login.html",
    "podcast_reader_premium/templates/user_detail.html",
    "podcast_reader_premium/templates/users.html",
}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: assert_packaged_ui.py WHEEL_DIRECTORY")
    wheels = sorted(Path(sys.argv[1]).glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one wheel, found {len(wheels)}")
    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    missing = EXPECTED - names
    if missing:
        raise SystemExit(f"premium wheel is missing UI assets: {sorted(missing)}")
    print(f"premium wheel UI assets verified: {len(EXPECTED)}")


if __name__ == "__main__":
    main()
