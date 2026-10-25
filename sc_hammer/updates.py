"""Is a newer release out? Asked once per run (claim_check), off the UI thread, by release builds only.

Only a packaged release (the AppImage or the Windows .exe, version X.Y.Z) asks: running from source or a
preview build carries the version in the code, which is the first-release default and would always look
out of date. Any failure (offline, rate limited, odd reply) just means no message."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from typing import Optional, Tuple

RELEASES_PAGE = "https://github.com/MisterAnderson91/sc-hammer/releases/latest"
LATEST_API = "https://api.github.com/repos/MisterAnderson91/sc-hammer/releases/latest"

_claimed = False


def claim_check(version: str) -> bool:
    """True exactly once per run, and only for a release build: whoever gets True does the check."""
    global _claimed
    if _claimed or not is_release_build(version):
        return False
    _claimed = True
    return True


def parse_version(v: str) -> Optional[Tuple[int, int, int]]:
    """(major, minor, patch) for "1.2.3" or "v1.2.3"; None for anything else, like a preview."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", v.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_release_build(version: str) -> bool:
    packaged = bool(getattr(sys, "frozen", False) or os.environ.get("APPIMAGE"))
    return packaged and parse_version(version) is not None


def newer_release(current: str, timeout: float = 5.0) -> Optional[str]:
    """The latest release's version if it's newer than `current`, else None (also on any error)."""
    mine = parse_version(current)
    if mine is None:
        return None
    try:
        req = urllib.request.Request(LATEST_API, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "sc-hammer/" + current})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            tag = str(json.load(r).get("tag_name", ""))
    except Exception:
        return None
    latest = parse_version(tag)
    return tag.lstrip("v") if latest and latest > mine else None
