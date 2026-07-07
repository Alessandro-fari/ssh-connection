"""
Targeted console input injection for Windows.

Writes text directly into the input buffer of a specific console process
(via AttachConsole + WriteConsoleInput), so the password ends up ONLY in
that terminal, regardless of which window has focus. It also reads the
console screen buffer to wait for the actual "password:" prompt instead
of sleeping a fixed amount of time.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

kernel32 = ctypes.windll.kernel32

# Console attachment is process-wide state: only one injection at a time.
_console_lock = threading.Lock()

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value

KEY_EVENT = 0x0001
VK_RETURN = 0x0D


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]


class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", _COORD), ("dwCursorPosition", _COORD),
                ("wAttributes", wt.WORD), ("srWindow", _SMALL_RECT),
                ("dwMaximumWindowSize", _COORD)]


class _CHAR_UNION(ctypes.Union):
    _fields_ = [("UnicodeChar", wt.WCHAR), ("AsciiChar", ctypes.c_char)]


class _KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wt.BOOL), ("wRepeatCount", wt.WORD),
                ("wVirtualKeyCode", wt.WORD), ("wVirtualScanCode", wt.WORD),
                ("uChar", _CHAR_UNION), ("dwControlKeyState", wt.DWORD)]


class _EVENT_UNION(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT_RECORD)]


class _INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wt.WORD), ("Event", _EVENT_UNION)]


class ConsoleInjector:
    """Injects keystrokes into the console owned by a specific PID."""

    PROMPT_TOKENS = ("password", "passphrase", "passcode")
    HOSTKEY_TOKEN = "yes/no"

    @classmethod
    def inject_password(cls, pid: int, password: str, timeout: float = 30.0) -> bool:
        """
        Wait for a password prompt in the console of `pid`, then type the
        password followed by Enter — directly into that console's input
        buffer. Returns True on success.
        """
        with _console_lock:
            return cls._inject_locked(pid, password, timeout)

    # ------------------------------------------------------------------

    @classmethod
    def _inject_locked(cls, pid: int, password: str, timeout: float) -> bool:
        original_pid = cls._sibling_console_pid()
        attached = False
        try:
            attached = cls._attach(pid, timeout=min(10.0, timeout))
            if not attached:
                logging.warning(f"Could not attach to console of PID {pid}")
                return False

            conout = cls._open_console("CONOUT$")
            conin = cls._open_console("CONIN$")
            if conout is None or conin is None:
                logging.warning("Could not open console buffers")
                return False

            try:
                answered_hostkey = False
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if not cls._pid_alive(pid):
                        logging.warning(f"Console process {pid} exited before prompt")
                        return False

                    tail = cls._read_tail(conout, lines=4).lower()

                    if not answered_hostkey and cls.HOSTKEY_TOKEN in tail:
                        cls._write_text(conin, "yes")
                        cls._write_key(conin, VK_RETURN, "\r")
                        answered_hostkey = True
                        time.sleep(0.4)
                        continue

                    if any(tok in tail for tok in cls.PROMPT_TOKENS):
                        cls._write_text(conin, password)
                        cls._write_key(conin, VK_RETURN, "\r")
                        logging.info(f"Password injected into console of PID {pid}")
                        return True

                    time.sleep(0.15)

                logging.warning(f"Timed out waiting for password prompt (PID {pid})")
                return False
            finally:
                kernel32.CloseHandle(conout)
                kernel32.CloseHandle(conin)
        finally:
            # _attach() always frees our console first, so re-attach the
            # original one even when attaching to the target failed.
            kernel32.FreeConsole()
            if original_pid:
                kernel32.AttachConsole(wt.DWORD(original_pid))

    @classmethod
    def run_command_at_shell_prompt(cls, pid: int, command: str, timeout: float = 240.0) -> bool:
        """
        Wait until a shell prompt (line ending in `$` or `#`) appears in the
        console of `pid` — i.e. the login, including any manual 2FA token
        entry, has completed — then type `command` + Enter into it.

        Attaches only in short bursts so concurrent password injections into
        other consoles are not blocked while waiting.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not cls._pid_alive(pid):
                logging.info(f"Console {pid} closed before shell prompt appeared")
                return False

            with _console_lock:
                tail = cls._peek_tail(pid)

            if tail is not None:
                last_line = next((l.strip() for l in reversed(tail.splitlines()) if l.strip()), "")
                if last_line.endswith(("$", "#")):
                    with _console_lock:
                        ok = cls._type_into(pid, command)
                    if ok:
                        logging.info(f"Keepalive command sent to console {pid}: {command}")
                    return ok

            time.sleep(1.0)

        logging.warning(f"Shell prompt never appeared in console {pid}, keepalive not sent")
        return False

    @classmethod
    def _peek_tail(cls, pid: int, lines: int = 4):
        """Attach briefly, read the last console lines, detach. None on failure."""
        original_pid = cls._sibling_console_pid()
        try:
            if not cls._attach(pid, timeout=1.0):
                return None
            conout = cls._open_console("CONOUT$")
            if conout is None:
                return None
            try:
                return cls._read_tail(conout, lines)
            finally:
                kernel32.CloseHandle(conout)
        finally:
            kernel32.FreeConsole()
            if original_pid:
                kernel32.AttachConsole(wt.DWORD(original_pid))

    @classmethod
    def _type_into(cls, pid: int, command: str) -> bool:
        """Attach and write `command` + Enter into the console of `pid`."""
        original_pid = cls._sibling_console_pid()
        try:
            if not cls._attach(pid, timeout=2.0):
                return False
            conin = cls._open_console("CONIN$")
            if conin is None:
                return False
            try:
                cls._write_text(conin, command)
                cls._write_key(conin, VK_RETURN, "\r")
                return True
            finally:
                kernel32.CloseHandle(conin)
        finally:
            kernel32.FreeConsole()
            if original_pid:
                kernel32.AttachConsole(wt.DWORD(original_pid))

    # ------------------------------------------------------------------

    @staticmethod
    def _sibling_console_pid():
        """PID of another process sharing our current console (to re-attach later)."""
        buf = (wt.DWORD * 64)()
        count = kernel32.GetConsoleProcessList(buf, 64)
        me = kernel32.GetCurrentProcessId()
        for i in range(min(count, 64)):
            if buf[i] != me:
                return buf[i]
        return None

    @staticmethod
    def _attach(pid: int, timeout: float) -> bool:
        """Detach from our console and attach to the target's, with retry
        while the target console is still starting up."""
        kernel32.FreeConsole()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if kernel32.AttachConsole(wt.DWORD(pid)):
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _open_console(name: str):
        handle = kernel32.CreateFileW(
            name, GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None)
        if handle == INVALID_HANDLE_VALUE:
            return None
        return handle

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        import psutil
        try:
            return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
        except Exception:
            return False

    @staticmethod
    def _read_tail(conout, lines: int = 4) -> str:
        """Read the last few lines of the console screen buffer as text."""
        info = _CONSOLE_SCREEN_BUFFER_INFO()
        if not kernel32.GetConsoleScreenBufferInfo(conout, ctypes.byref(info)):
            return ""
        width = info.dwSize.X
        end_y = info.dwCursorPosition.Y
        start_y = max(0, end_y - lines + 1)
        chunks = []
        for y in range(start_y, end_y + 1):
            buf = ctypes.create_unicode_buffer(width)
            read = wt.DWORD(0)
            coord = _COORD(0, y)
            if kernel32.ReadConsoleOutputCharacterW(conout, buf, width, coord, ctypes.byref(read)):
                chunks.append(buf.value[:read.value])
        return "\n".join(chunks)

    @classmethod
    def _write_text(cls, conin, text: str) -> None:
        # vk=0 with UnicodeChar set: the console reads the character as-is,
        # with no keyboard-layout translation that could alter it.
        cls._write_events(conin, [(0, ch) for ch in text])

    @classmethod
    def _write_key(cls, conin, vk: int, ch: str) -> None:
        cls._write_events(conin, [(vk, ch)])

    @staticmethod
    def _write_events(conin, keys) -> None:
        """Write down+up key events for all keys in a single atomic call."""
        count = len(keys) * 2
        records = (_INPUT_RECORD * count)()
        i = 0
        for vk, ch in keys:
            for key_down in (True, False):
                records[i].EventType = KEY_EVENT
                records[i].Event.KeyEvent.bKeyDown = key_down
                records[i].Event.KeyEvent.wRepeatCount = 1
                records[i].Event.KeyEvent.wVirtualKeyCode = vk
                records[i].Event.KeyEvent.wVirtualScanCode = 0
                records[i].Event.KeyEvent.uChar.UnicodeChar = ch
                records[i].Event.KeyEvent.dwControlKeyState = 0
                i += 1
        written = wt.DWORD(0)
        kernel32.WriteConsoleInputW(conin, ctypes.byref(records), count, ctypes.byref(written))
