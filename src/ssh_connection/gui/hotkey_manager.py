"""
Global hotkey manager (Win32 RegisterHotKey).

Runs a dedicated thread with its own Win32 message loop: pystray owns the
tray's message loop on the main thread and does not expose hooks into it,
so a separate GetMessage loop is the clean way to receive WM_HOTKEY.

Default binding: Ctrl+Shift+Space -> opens the host search dialog. The
binding is configurable from the settings dialog as a "Ctrl+Shift+Space"
style string (see `parse_binding`).
The hotkey is global (NULL window handle): Windows routes it to our thread
even when the app is in the background.
"""

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable, Optional, Tuple

# Win32 constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_SPACE = 0x20

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# Message posted by stop() to break GetMessage
WM_USER_STOP = 0x0400 + 1

MODIFIERS = (("Ctrl", MOD_CONTROL), ("Shift", MOD_SHIFT), ("Alt", MOD_ALT), ("Win", MOD_WIN))

# Keys offered by the settings dialog -> virtual-key code
KEYS = {"Space": VK_SPACE}
KEYS.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
KEYS.update({str(d): 0x30 + d for d in range(10)})
KEYS.update({f"F{n}": 0x6F + n for n in range(1, 13)})


def parse_binding(binding: str) -> Optional[Tuple[int, int]]:
    """'Ctrl+Shift+Space' -> (modifier flags, vk). None if invalid: at least
    one modifier is required (except F-keys), so plain typing is never eaten."""
    parts = [p.strip() for p in (binding or "").split("+") if p.strip()]
    if not parts:
        return None
    key = parts[-1].upper() if len(parts[-1]) == 1 else parts[-1].capitalize()
    if key not in KEYS:
        return None
    mods = 0
    names = {n.lower(): f for n, f in MODIFIERS}
    for p in parts[:-1]:
        flag = names.get(p.lower())
        if flag is None:
            return None
        mods |= flag
    if not mods and not key.startswith("F"):
        return None
    return mods, KEYS[key]


def format_binding(mods: int, key: str) -> str:
    """(modifier flags, key name) -> canonical 'Ctrl+Shift+Space' string."""
    return "+".join([n for n, f in MODIFIERS if mods & f] + [key])


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class HotkeyManager:
    """Registers a global hotkey and invokes a callback when pressed.

    The callback runs on the hotkey thread; keep it non-blocking or offload
    to another thread. It must not touch the pystray main thread directly
    (pystray callbacks run there).
    """

    _HOTKEY_ID = 1

    # Default binding; the live one is `self.binding` (shown as the
    # accelerator hint in the tray menu, so the label cannot drift from the
    # keys actually registered).
    SHORTCUT_LABEL = "Ctrl+Shift+Space"

    def __init__(self, on_triggered: Callable[[], None], binding: str = SHORTCUT_LABEL):
        self._on_triggered = on_triggered
        self.binding = binding if parse_binding(binding) else self.SHORTCUT_LABEL
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stopped = threading.Event()
        self._registered = threading.Event()
        self._reg_done = threading.Event()

    def start(self) -> bool:
        """Start listening. Returns whether RegisterHotKey succeeded (False
        e.g. when another application already owns the combination)."""
        if self._thread and self._thread.is_alive():
            return self._registered.is_set()
        self._stopped.clear()
        self._registered.clear()
        self._reg_done.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._reg_done.wait(timeout=2.0)
        logging.info(f"HotkeyManager started ({self.binding}, "
                     f"registered={self._registered.is_set()})")
        return self._registered.is_set()

    def rebind(self, binding: str) -> bool:
        """Switch to `binding`. On failure the previous binding is restored
        and False is returned."""
        if parse_binding(binding) is None:
            return False
        if binding == self.binding and self._registered.is_set():
            return True
        previous = self.binding
        self.stop()
        self.binding = binding
        if self.start():
            return True
        logging.warning(f"Hotkey {binding} unavailable, restoring {previous}")
        self.stop()
        self.binding = previous
        self.start()
        return False

    def stop(self) -> None:
        if not self._thread:
            return
        self._stopped.set()
        if self._thread_id is not None:
            # Wake GetMessage: post a message to this thread's queue.
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, WM_USER_STOP, 0, 0)
        self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = None
        logging.info("HotkeyManager stopped")

    def _run(self) -> None:
        # Ensure this thread has a message queue before PostThreadMessage
        # can target it: PeekMessage forces its creation.
        msg = _MSG()
        ctypes.windll.user32.PeekMessageW(
            ctypes.byref(msg), None, 0, 0, 0)  # PM_NOREMOVE
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        mods, vk = parse_binding(self.binding)
        ok = ctypes.windll.user32.RegisterHotKey(
            None, self._HOTKEY_ID, mods | MOD_NOREPEAT, vk)
        if not ok:
            err = ctypes.GetLastError()
            # 1409 = hotkey already registered by another app
            logging.error(f"RegisterHotKey failed (err={err}); "
                          f"another application may already bind {self.binding}")
            self._reg_done.set()
            return

        self._registered.set()
        self._reg_done.set()
        logging.info(f"Hotkey registered: {self.binding}")

        try:
            while True:
                got = ctypes.windll.user32.GetMessageW(
                    ctypes.byref(msg), None, 0, 0)
                if got <= 0:  # WM_QUIT or error
                    break
                if msg.message == WM_HOTKEY and msg.wParam == self._HOTKEY_ID:
                    logging.debug("Hotkey pressed")
                    try:
                        self._on_triggered()
                    except Exception as e:
                        logging.error(f"Hotkey callback failed: {e}",
                                      exc_info=True)
                elif msg.message == WM_USER_STOP:
                    break
        finally:
            ctypes.windll.user32.UnregisterHotKey(None, self._HOTKEY_ID)
            self._registered.clear()
            logging.debug("Hotkey unregistered")
