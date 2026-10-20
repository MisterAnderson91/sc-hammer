"""Nicknames and automatic backups, kept under the XDG config/data directories."""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from .model import Snapshot

APP_DIR = "sc-hammer"
OLD_APP_DIR = "openpuck-configurator"  # name before the rename to SC Hammer
MAX_BACKUPS = 100


def _xdg(var: str, fallback: str) -> str:
    base = os.environ.get(var) or os.path.expanduser(fallback)
    new, old = os.path.join(base, APP_DIR), os.path.join(base, OLD_APP_DIR)
    # Carry nicknames and backups over from the old name, once.
    if not os.path.exists(new) and os.path.isdir(old):
        try:
            os.rename(old, new)
        except OSError:
            return old
    return new


class Store:
    def __init__(self, config_dir: Optional[str] = None, data_dir: Optional[str] = None):
        self.config_dir = config_dir or _xdg("XDG_CONFIG_HOME", "~/.config")
        self.backup_dir = os.path.join(data_dir or _xdg("XDG_DATA_HOME", "~/.local/share"),
                                       "backups")
        self._names: Dict[str, str] = {}
        try:
            with open(os.path.join(self.config_dir, "names.json")) as f:
                self._names = {str(k): str(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError):
            pass

    # --- nicknames ---

    def name(self, serial: str) -> str:
        return self._names.get(serial, "")

    def set_name(self, serial: str, name: str) -> None:
        name = name.strip()
        if name:
            self._names[serial] = name
        else:
            self._names.pop(serial, None)
        os.makedirs(self.config_dir, exist_ok=True)
        tmp = os.path.join(self.config_dir, "names.json.tmp")
        with open(tmp, "w") as f:
            json.dump(self._names, f, indent=2, sort_keys=True)
        os.replace(tmp, os.path.join(self.config_dir, "names.json"))

    # --- backups ---

    def save_backup(self, snap: Snapshot, reason: str) -> str:
        now = time.time()
        data = {
            "version": 1,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "reason": reason,
            "receivers": [{
                "serial": r.serial, "kind": r.kind,
                "slots": [s.record.hex() if s.record is not None else None for s in r.slots],
            } for r in snap.receivers],
            "controllers": [{
                "serial": c.serial,
                "slots": [s.record.hex() if s.record is not None else None for s in c.slots],
            } for c in snap.controllers],
        }
        os.makedirs(self.backup_dir, exist_ok=True)
        path = os.path.join(self.backup_dir, time.strftime("backup-%Y%m%d-%H%M%S", time.localtime(now))
                            + "-%03d.json" % int((now % 1) * 1000))
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self._prune()
        return path

    def _files(self) -> List[str]:
        try:
            names = [n for n in os.listdir(self.backup_dir)
                     if n.startswith("backup-") and n.endswith(".json")]
        except OSError:
            return []
        return [os.path.join(self.backup_dir, n) for n in sorted(names, reverse=True)]

    def _prune(self) -> None:
        for path in self._files()[MAX_BACKUPS:]:
            try:
                os.remove(path)
            except OSError:
                pass

    def backups(self) -> List[dict]:
        """Newest first. Each dict gets "path" and "created_display" added."""
        out = []
        for path in self._files():
            try:
                with open(path) as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            data["path"] = path
            try:
                t = time.strptime(data.get("created", ""), "%Y-%m-%dT%H:%M:%S")
                data["created_display"] = time.strftime("%a %d %b %Y, %H:%M:%S", t)
            except ValueError:
                data["created_display"] = os.path.basename(path)
            out.append(data)
        return out
