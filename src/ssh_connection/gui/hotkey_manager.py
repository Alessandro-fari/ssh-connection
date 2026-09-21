"""
Global hotkey manager (Win32 RegisterHotKey).

Runs a dedicated thread with its own Win32 message loop: pystray owns the
tray's message loop on the main thread and does not expose hooks into it,
so a separate GetMessage loop is the clean way to receive WM_HOTKEY.

Default binding: Ctrl+Shift+Space -> opens the host search dialog.
The hotkey is global (NULL window handle): Windows routes it to our thread
even when the app is in the background.
"""

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

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

    # Human-readable binding, shown as the accelerator hint in the tray menu.
    # Kept next to the registration below so the label cannot drift from the
    # keys actually registered.
    SHORTCUT_LABEL = "Ctrl+Shift+Space"

    def __init__(self, on_triggered: Callable[[], None]):
        self._on_triggered = on_triggered
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logging.info("HotkeyManager started")

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

        mods = MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
        ok = ctypes.windll.user32.RegisterHotKey(
            None, self._HOTKEY_ID, mods, VK_SPACE)
        if not ok:
            err = ctypes.get_last_error()
            # 1409 = hotkey already registered by another app
            logging.error(f"RegisterHotKey failed (err={err}); "
                          "another application may already bind Ctrl+Shift+Space")
            return

        logging.info("Hotkey registered: Ctrl+Shift+Space")

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
            logging.debug("Hotkey unregistered")
