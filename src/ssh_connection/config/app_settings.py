"""
User preferences of the application (not the SSH config, not the credentials).

Stored as JSON in ~/.ssh_connection_prefs.json — the same file the search
popup already used for its environment filter, so existing installs keep
their saved `search_env`. Every write is a read-modify-write of the whole
document under a lock, so a component saving its own key can never clobber
the keys of another one.

Keys (all optional in the file; defaults below):
  search_env          'Tutti' | 'TEST' | 'PROD' — popup environment filter
  hotkey              e.g. 'Ctrl+Shift+Space' — global search shortcut
  keepalive_interval  seconds between `date` runs of the keepalive `watch`
  tunnel_timeout      seconds to wait for a jump host's tunnel port
  notifications       {kind: bool} — see NOTIFICATION_KINDS
  favorites           [host, ...] — pinned at the top of menu and popup
  recents             [host, ...] — most recent first, at most MAX_RECENTS
  prod_console_theme  colour scheme of PROD consoles ('Nessuno' = none),
                      see ssh.console_themes
  file_search_paths   {host: [path, ...]} — remote folders used in the file
                      search window, most recent first (MAX_FILE_PATHS)
  dark_theme          bool — One Half Dark theme for the app windows
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

PREFS_FILE = Path.home() / ".ssh_connection_prefs.json"

MAX_RECENTS = 5
MAX_FILE_PATHS = 10

# kind -> label shown in the settings dialog
NOTIFICATION_KINDS = {
    "init": "Esito di Init TEST / PROD",
    "connection_lost": "Connessione chiusa inaspettatamente",
    "tunnel_lost": "Tunnel perso (porta LocalForward non in ascolto)",
    "keepalive_failed": "Keepalive non avviato",
}

DEFAULTS: Dict[str, Any] = {
    "search_env": "Tutti",
    "hotkey": "Ctrl+Shift+Space",
    "keepalive_interval": 240,
    "tunnel_timeout": 120,
    "notifications": {k: True for k in NOTIFICATION_KINDS},
    "favorites": [],
    "recents": [],
    "prod_console_theme": "Ubuntu-ColorScheme",
    "file_search_paths": {},
    "dark_theme": False,
}

# (min, max) accepted for the numeric settings
LIMITS = {
    "keepalive_interval": (30, 3600),
    "tunnel_timeout": (10, 600),
}


class AppSettings:
    """Thread-safe access to the preferences file (all classmethods)."""

    _lock = threading.RLock()
    path: Path = PREFS_FILE

    # ------------------------------------------------------------------
    # Raw document

    @classmethod
    def _read(cls) -> Dict[str, Any]:
        try:
            data = json.loads(cls.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            logging.warning(f"Unreadable preferences file {cls.path}: {e}")
            return {}

    @classmethod
    def _write(cls, data: Dict[str, Any]) -> None:
        # Write-then-rename: a crash mid-write never leaves a truncated file.
        tmp = cls.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, cls.path)

    # ------------------------------------------------------------------
    # Generic get / update

    @classmethod
    def get(cls, key: str) -> Any:
        with cls._lock:
            value = cls._read().get(key, DEFAULTS.get(key))
        return cls._sanitize(key, value)

    @classmethod
    def all(cls) -> Dict[str, Any]:
        with cls._lock:
            data = cls._read()
        return {k: cls._sanitize(k, data.get(k, DEFAULTS[k])) for k in DEFAULTS}

    @classmethod
    def update(cls, **values: Any) -> None:
        """Merge `values` into the file (other keys are preserved)."""
        with cls._lock:
            data = cls._read()
            for key, value in values.items():
                data[key] = cls._sanitize(key, value)
            try:
                cls._write(data)
            except Exception as e:
                logging.error(f"Could not save preferences: {e}")

    @staticmethod
    def _sanitize(key: str, value: Any) -> Any:
        default = DEFAULTS.get(key)
        if key in LIMITS:
            lo, hi = LIMITS[key]
            try:
                return max(lo, min(hi, int(value)))
            except (TypeError, ValueError):
                return default
        if key == "notifications":
            merged = dict(default)
            if isinstance(value, dict):
                merged.update({k: bool(v) for k, v in value.items() if k in merged})
            return merged
        if key in ("favorites", "recents"):
            if not isinstance(value, list):
                return []
            seen, out = set(), []
            for h in value:
                if isinstance(h, str) and h and h not in seen:
                    seen.add(h)
                    out.append(h)
            return out[:MAX_RECENTS] if key == "recents" else out
        if key == "search_env":
            return value if value in ("Tutti", "TEST", "PROD") else default
        if key in ("hotkey", "prod_console_theme"):
            return value if isinstance(value, str) and value else default
        if key == "dark_theme":
            return value if isinstance(value, bool) else default
        if key == "file_search_paths":
            if not isinstance(value, dict):
                return {}
            out = {}
            for host, paths in value.items():
                if isinstance(host, str) and isinstance(paths, list):
                    clean = []
                    for p in paths:
                        if isinstance(p, str) and p.strip() and p.strip() not in clean:
                            clean.append(p.strip())
                    if clean:
                        out[host] = clean[:MAX_FILE_PATHS]
            return out
        return value

    # ------------------------------------------------------------------
    # Typed helpers

    @classmethod
    def notification_enabled(cls, kind: str) -> bool:
        return cls.get("notifications").get(kind, True)

    @classmethod
    def favorites(cls) -> List[str]:
        return cls.get("favorites")

    @classmethod
    def is_favorite(cls, host: str) -> bool:
        return host in cls.favorites()

    @classmethod
    def toggle_favorite(cls, host: str) -> bool:
        """Add/remove `host` from the favorites. Returns the new state."""
        with cls._lock:
            favs = cls.favorites()
            if host in favs:
                favs.remove(host)
                now = False
            else:
                favs.append(host)
                now = True
            cls.update(favorites=favs)
        return now

    @classmethod
    def recents(cls) -> List[str]:
        return cls.get("recents")

    @classmethod
    def add_recent(cls, host: str) -> None:
        """Move `host` to the front of the recents list."""
        with cls._lock:
            rec = [h for h in cls.recents() if h != host]
            cls.update(recents=[host] + rec)

    @classmethod
    def file_search_paths(cls, host: str) -> List[str]:
        """Remote folders searched on `host`, most recent first."""
        return list(cls.get("file_search_paths").get(host, []))

    @classmethod
    def add_file_search_path(cls, host: str, path: str) -> None:
        """Move `path` to the front of `host`'s folder history."""
        path = path.strip()
        if not path:
            return
        with cls._lock:
            all_paths = cls.get("file_search_paths")
            all_paths[host] = [path] + [p for p in all_paths.get(host, []) if p != path]
            cls.update(file_search_paths=all_paths)
