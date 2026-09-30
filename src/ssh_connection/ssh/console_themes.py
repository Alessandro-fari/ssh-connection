"""
Colour schemes for the consoles opened by the launcher (PROD highlighting).

A scheme is applied from inside the console with escape sequences, so it
works both in Windows Terminal and in the classic conhost, and it does not
require launching the terminal with a profile (we must own the PID of the
console process — see SshLauncher._launch):

  OSC 4;n;rgb:..   the 16 palette entries (ANSI colours used by ls, git, ...)
  OSC 10 / 11 / 12 default foreground / background / cursor colour

Setting the *default* colours (not the current attribute) is what keeps the
whole session themed: SGR 0 and `clear` coming from the remote shell fall
back to them.

Schemes use the Windows Terminal JSON format. Besides the built-in ones, the
schemes defined in the user's Windows Terminal settings.json are offered too.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

NO_THEME = "Nessuno"
DEFAULT_PROD_THEME = "Ubuntu-ColorScheme"

# Windows Terminal key order == ANSI palette index 0..15
_PALETTE_KEYS = (
    "black", "red", "green", "yellow", "blue", "purple", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow",
    "brightBlue", "brightPurple", "brightCyan", "brightWhite",
)

BUILTIN_SCHEMES: Dict[str, Dict[str, str]] = {
    # As shipped by Windows Terminal ("Ubuntu-ColorScheme").
    "Ubuntu-ColorScheme": {
        "background": "#300A24", "foreground": "#FFFFFF", "cursorColor": "#FFFFFF",
        "black": "#171421", "red": "#C21A23", "green": "#26A269", "yellow": "#A2734C",
        "blue": "#0037DA", "purple": "#881798", "cyan": "#3A96DD", "white": "#CCCCCC",
        "brightBlack": "#767676", "brightRed": "#C01C28", "brightGreen": "#26A269",
        "brightYellow": "#A2734C", "brightBlue": "#08458F", "brightPurple": "#A347BA",
        "brightCyan": "#2C9FB3", "brightWhite": "#F2F2F2",
    },
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _wt_settings_path() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json"


def _user_schemes() -> Dict[str, Dict[str, str]]:
    """Schemes defined in Windows Terminal's settings.json (may be absent)."""
    try:
        text = _wt_settings_path().read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    try:
        # settings.json allows // comments on their own line
        data = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))
    except ValueError as e:
        logging.debug(f"Unreadable Windows Terminal settings: {e}")
        return {}
    out = {}
    for s in data.get("schemes") or []:
        if isinstance(s, dict) and isinstance(s.get("name"), str):
            out[s["name"]] = s
    return out


def available_schemes() -> Dict[str, Dict[str, str]]:
    schemes = dict(BUILTIN_SCHEMES)
    schemes.update(_user_schemes())
    return schemes


def scheme_names() -> List[str]:
    """Choices for the settings dialog: 'Nessuno' first, then the schemes."""
    return [NO_THEME] + sorted(available_schemes(), key=str.lower)


def _rgb(color: str) -> Optional[str]:
    if not (isinstance(color, str) and _HEX.match(color)):
        return None
    return f"rgb:{color[1:3]}/{color[3:5]}/{color[5:7]}".lower()


def osc_sequences(name: str) -> Optional[str]:
    """Escape sequences applying scheme `name` (with \\x1b / \\x07), or None
    for 'Nessuno' / an unknown scheme."""
    if not name or name == NO_THEME:
        return None
    scheme = available_schemes().get(name)
    if scheme is None:
        logging.warning(f"Console scheme {name!r} not found, console left unthemed")
        return None
    seq = []
    for i, key in enumerate(_PALETTE_KEYS):
        rgb = _rgb(scheme.get(key))
        if rgb:
            seq.append(f"\x1b]4;{i};{rgb}\x07")
    for code, key in ((10, "foreground"), (11, "background"), (12, "cursorColor")):
        rgb = _rgb(scheme.get(key))
        if rgb:
            seq.append(f"\x1b]{code};{rgb}\x07")
    return "".join(seq) or None
