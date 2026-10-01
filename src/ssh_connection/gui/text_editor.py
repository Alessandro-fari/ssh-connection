"""
The program that opens the text files of the app: "Apri" of the SSH config,
the credentials, the preferences and the log in the settings, and the files
opened with a double click in "Cerca file".

Works like Windows' "Apri con": while the preference `text_editor` is empty
("Chiedi ogni volta") a small chooser lists the editors found on this PC —
Blocco note, the programs Windows uses for .txt and .log files (e.g. a log
viewer), Notepad++, Visual Studio Code — plus "Sfoglia..." for any other
.exe. "Solo questa volta"
opens the file with it, "Sempre" also saves it, so the next files open
straight away. Impostazioni -> Generale changes it later or goes back to
"Chiedi ogni volta".

An app-level choice rather than the Windows association: the config has no
extension and the remote files have all sorts of them (.log, .out, .1,
.xml...), so one editor for all of them is what the user expects, and the
app keeps the process handle it needs to delete the temporary copies.

Everything here runs on the Tk thread (the chooser is a modal Toplevel).
"""

import ctypes
import logging
import os
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import List, Optional, Tuple

from ..config.app_settings import AppSettings
from . import theme
from .theme import px

ASK = ""           # value of the preference meaning "Chiedi ogni volta"
ASK_LABEL = "Chiedi ogni volta"

_KNOWN = {"notepad": "Blocco note", "notepad++": "Notepad++", "code": "Visual Studio Code",
          "sublime_text": "Sublime Text", "wordpad": "WordPad", "klogg": "klogg"}


def notepad_path() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "notepad.exe")


def editor_label(exe: str) -> str:
    return _KNOWN.get(Path(exe).stem.lower(), Path(exe).stem)


def _assoc_executable(ext: str) -> Optional[str]:
    """Program Windows opens `ext` files with (honours the user's choice)."""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        # ASSOCF_NONE, ASSOCSTR_EXECUTABLE
        if ctypes.windll.shlwapi.AssocQueryStringW(0, 2, ext, "open", buf,
                                                   ctypes.byref(size)) == 0 and buf.value:
            return buf.value
    except Exception as e:
        logging.debug(f"AssocQueryString({ext}) failed: {e}")
    return None


def _app_path(exe_name: str) -> Optional[str]:
    """Install path registered under App Paths (HKCU, then HKLM)."""
    try:
        import winreg
    except ImportError:
        return None
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe_name
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as k:
                value = winreg.QueryValue(k, None).strip('"')
            if value and Path(value).is_file():
                return value
        except OSError:
            continue
    return None


def find_editors() -> List[Tuple[str, str]]:
    """(label, exe) of the editors found, Blocco note first, no duplicates."""
    local = os.environ.get("LOCALAPPDATA", "")
    defaults = {ext: _assoc_executable(ext) for ext in (".txt", ".log")}
    candidates = [notepad_path(), defaults[".txt"], defaults[".log"],
                  _app_path("notepad++.exe"),
                  r"C:\Program Files\Notepad++\notepad++.exe",
                  r"C:\Program Files (x86)\Notepad++\notepad++.exe",
                  _app_path("Code.exe"),
                  str(Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe") if local else None,
                  r"C:\Program Files\Microsoft VS Code\Code.exe",
                  _app_path("sublime_text.exe"),
                  r"C:\Program Files\Sublime Text\sublime_text.exe"]
    def norm(p):
        return os.path.normcase(os.path.abspath(p))
    out, seen = [], set()
    for exe in candidates:
        if not exe or not Path(exe).is_file():
            continue
        key = norm(exe)
        if key in seen:
            continue
        seen.add(key)
        label = editor_label(exe)
        exts = [e for e, d in defaults.items() if d and norm(d) == key]
        if exts:
            label += f" (predefinito di Windows per {' e '.join(exts)})"
        out.append((label, exe))
    return out


def configured_editor() -> Optional[str]:
    """The editor saved with "Sempre", if it still exists."""
    exe = AppSettings.get("text_editor") or ASK
    if exe and Path(exe).is_file():
        return exe
    return None


def resolve_editor(parent=None, file_name: str = "") -> Optional[str]:
    """The saved editor, or the one picked in "Apri con" (None = cancelled)."""
    return configured_editor() or EditorChooser(parent, file_name).ask()


def launch(exe: str, path: Path) -> Optional[subprocess.Popen]:
    """Start `exe path`. If it cannot be started, Windows' association is
    tried instead and None is returned."""
    try:
        return subprocess.Popen([exe, str(path)], stdin=subprocess.DEVNULL)
    except OSError as e:
        logging.error(f"Cannot start {exe} for {path}: {e}")
        try:
            os.startfile(str(path))
        except OSError as e2:
            logging.error(f"Cannot open {path}: {e2}")
        return None


def open_text(path: Path, parent=None) -> bool:
    """Open `path` with the chosen editor, asking first if none is saved.
    False if the user cancelled the choice."""
    exe = resolve_editor(parent, Path(path).name)
    if exe is None:
        return False
    launch(exe, path)
    return True


def browse_executable(parent) -> Optional[str]:
    from tkinter import filedialog
    exe = filedialog.askopenfilename(
        parent=parent, title="Scegli il programma per i file di testo",
        initialdir=os.environ.get("ProgramFiles", "C:\\"),
        filetypes=[("Programmi", "*.exe"), ("Tutti i file", "*.*")])
    return os.path.normpath(exe) if exe else None


class EditorChooser:
    """Modal "Apri con": pick an editor, then Solo questa volta / Sempre."""

    def __init__(self, parent, file_name: str = ""):
        self._parent = parent
        self._file_name = file_name
        self._result: Optional[str] = None

    def ask(self) -> Optional[str]:
        import tkinter as tk

        from . import widgets as w
        parent = self._parent
        top = tk.Toplevel(parent)
        self._top = top
        top.title("Apri con")
        theme.toplevel(top)
        if parent is not None:
            top.transient(parent.winfo_toplevel())
        top.resizable(False, False)
        body = theme.style(tk.Frame(top, padx=px(24), pady=px(20)), "window")
        body.pack(fill="both", expand=True)
        w.page_title(body, "Con cosa vuoi aprire i file di testo?").pack(fill="x")
        w.label(body, (f"{self._file_name}\n" if self._file_name else "") +
                "Vale per il config, i log e i file aperti da Cerca file. \"Sempre\" lo "
                "ricorda; si cambia in Impostazioni → Generale.", "hint", size=9, anchor="w",
                justify="left", wraplength=px(420)).pack(fill="x", pady=(px(2), px(12)))

        self._editors = find_editors()
        lv = w.ListView(body, height=max(3, min(6, len(self._editors))))
        lv.pack(fill="x")
        for label, exe in self._editors:
            lv.insert("end", f"  {label}")
        if self._editors:
            lv.selection_set(0)
        lv.bind("<Double-Button-1>", lambda e: self._pick(remember=False))
        self._list = lv

        other = theme.style(tk.Frame(body), "window")
        other.pack(fill="x", pady=(px(8), 0))
        w.Button(other, "Sfoglia...", self._browse).pack(side="left")
        w.label(other, "un altro programma (.exe)", "hint", size=9).pack(side="left",
                                                                         padx=(px(8), 0))
        btns = theme.style(tk.Frame(body), "window")
        btns.pack(fill="x", pady=(px(18), 0))
        w.Button(btns, "Sempre", lambda: self._pick(remember=True), "primary",
                 width=10).pack(side="right")
        w.Button(btns, "Solo questa volta", lambda: self._pick(remember=False)).pack(
            side="right", padx=(0, px(8)))
        w.Button(btns, "Annulla", top.destroy, width=9).pack(side="right", padx=(0, px(8)))
        top.bind("<Escape>", lambda e: top.destroy())
        top.bind("<Return>", lambda e: self._pick(remember=False))

        top.update_idletasks()
        if parent is not None and parent.winfo_viewable():
            ref = parent.winfo_toplevel()
            x = ref.winfo_rootx() + (ref.winfo_width() - top.winfo_reqwidth()) // 2
            y = ref.winfo_rooty() + max(0, (ref.winfo_height() - top.winfo_reqheight()) // 3)
        else:
            x = (top.winfo_screenwidth() - top.winfo_reqwidth()) // 2
            y = (top.winfo_screenheight() - top.winfo_reqheight()) // 3
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.lift()
        top.grab_set()
        lv.focus_set()
        top.wait_window()
        return self._result

    def _selected(self) -> Optional[str]:
        sel = self._list.curselection()
        return self._editors[sel[0]][1] if sel and self._editors else None

    def _browse(self) -> None:
        exe = browse_executable(self._top)
        if exe:
            self._editors.append((editor_label(exe), exe))
            self._list.insert("end", f"  {editor_label(exe)}  ({exe})")
            self._list.selection_clear()
            self._list.selection_set(len(self._editors) - 1)
            self._list.see(len(self._editors) - 1)

    def _pick(self, remember: bool) -> None:
        exe = self._selected()
        if exe is None:
            return
        if remember:
            AppSettings.update(text_editor=exe)
        self._result = exe
        self._top.destroy()
