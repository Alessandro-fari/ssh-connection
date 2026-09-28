"""
Central tray notifications, filtered by the user's per-kind preferences.

Components below the GUI (Init orchestrator, launcher, session monitor)
call `notify(kind, title, message)` without knowing about pystray; the tray
installs the actual balloon function with `set_sink()` at startup. Kinds are
the keys of `app_settings.NOTIFICATION_KINDS`.
"""

import logging
from typing import Callable, Optional

from .config.app_settings import AppSettings

_sink: Optional[Callable[[str, str], None]] = None


def set_sink(fn: Optional[Callable[[str, str], None]]) -> None:
    global _sink
    _sink = fn


def notify(kind: str, title: str, message: str) -> None:
    logging.info(f"Notification [{kind}] {title}: {message}")
    if not AppSettings.notification_enabled(kind):
        return
    if _sink is None:
        return
    try:
        _sink(title, message)
    except Exception as e:
        logging.debug(f"Notification sink failed: {e}")
