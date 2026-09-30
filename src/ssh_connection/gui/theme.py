"""
Look of the Tk windows (host popup, "Cerca file", Impostazioni): Windows 11
style, light or dark (One Half Dark), and DPI scaling.

DPI: the process declares itself DPI aware (`enable_dpi_awareness`, called
by main before any window exists). Without it Windows renders the windows
at 96 DPI and stretches the bitmap on a 125%/150% screen, which is why the
text looked blurry. Once aware, Tk scales the fonts (sizes in points) by
itself, but sizes in pixels (geometry, fixed widths, wraplength, Treeview
rows, images) do not follow: they go through `px()`.

Themes: "light" is a Windows 11 palette (accent #005fb8); "dark" follows the
One Half Dark scheme of Windows Terminal. The font is Segoe UI in both.
Widgets are not built with colours: they are registered with a *role* and
a *surface* (`style(widget, "label", "card")`: a label drawn on a card),
and the palette of the current theme is applied to them, so `use(dark)`
repaints every open window at once. Colours that change with the state
(status in error, busy search button, TEST/PROD label) switch role with
`set_role`. Buttons get a hover colour. ttk widgets use the "clam" theme,
restyled here, the only one whose colours can be changed.

Everything here runs on the Tk thread (the SearchPopup one).
"""

import ctypes
import logging
from typing import Callable, Dict, List, Optional

FONT = "Segoe UI"
FONT_SEMIBOLD = "Segoe UI Semibold"
ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")   # Windows 11, Windows 10

PALETTES: Dict[str, Dict[str, str]] = {
    "light": {
        "bg": "#f3f3f3", "nav": "#ebebeb", "nav_sel": "#dedede",
        "card": "#fbfbfb", "card_border": "#e5e5e5", "divider": "#eaeaea",
        "text": "#1b1b1b", "muted": "#5f5f5f", "faint": "#8a8a8a",
        "field": "#ffffff", "border": "#d1d1d1", "focus": "#005fb8",
        "sel_bg": "#cfe3f6", "sel_fg": "#1b1b1b", "sep_fg": "#5f5f5f",
        "accent": "#005fb8", "accent_fg": "#ffffff", "accent_hover": "#1a6fc0",
        "btn": "#fdfdfd", "btn_fg": "#1b1b1b", "btn_hover": "#f0f0f0", "btn_border": "#d0d0d0",
        "btn_disabled": "#a0a0a0", "subtle_hover": "#e3e3e3",
        "danger": "#c42b1c", "danger_fg": "#ffffff", "danger_hover": "#b3281a",
        "error": "#c42b1c", "test": "#0f7b0f", "prod": "#9d5d00",
        "test_bg": "#dff6dd", "prod_bg": "#fff4ce",
        "banner_bg": "#e6f0fa", "banner_fg": "#1b1b1b",
        "toggle_off": "#8a8a8a",
    },
    # One Half Dark (Windows Terminal): background #282c34, foreground
    # #dcdfe4, blue #61afef, green #98c379, yellow #e5c07b, red #e06c75.
    "dark": {
        "bg": "#282c34", "nav": "#21252b", "nav_sel": "#2f343e",
        "card": "#2c313a", "card_border": "#363c47", "divider": "#363c47",
        "text": "#dcdfe4", "muted": "#9aa1ad", "faint": "#6b717d",
        "field": "#21252b", "border": "#4b5263", "focus": "#61afef",
        "sel_bg": "#3a4a63", "sel_fg": "#ffffff", "sep_fg": "#9aa1ad",
        "accent": "#61afef", "accent_fg": "#1e2127", "accent_hover": "#7dbdf2",
        "btn": "#343a45", "btn_fg": "#dcdfe4", "btn_hover": "#3d4450", "btn_border": "#454c59",
        "btn_disabled": "#6b717d", "subtle_hover": "#353b46",
        "danger": "#e06c75", "danger_fg": "#1e2127", "danger_hover": "#e8878f",
        "error": "#e06c75", "test": "#98c379", "prod": "#e5c07b",
        "test_bg": "#2f3b2b", "prod_bg": "#3d3526",
        "banner_bg": "#2c3a4d", "banner_fg": "#dcdfe4",
        "toggle_off": "#9aa1ad",
    },
}


def _button(p, kind):
    return dict(bg=p[kind], fg=p[kind + "_fg"], activebackground=p[kind + "_hover"],
                activeforeground=p[kind + "_fg"], disabledforeground=p["btn_disabled"],
                highlightthickness=1, highlightbackground=p[kind], highlightcolor=p["focus"],
                bd=0, relief="flat", overrelief="flat")


# role -> options of the Tk (not ttk) widget, from the palette `p` and the
# colour of the surface the widget sits on `s`
ROLES: Dict[str, Callable[[Dict[str, str], str], dict]] = {
    "window": lambda p, s: dict(bg=s),
    "card": lambda p, s: dict(bg=p["card"], highlightthickness=1,
                              highlightbackground=p["card_border"],
                              highlightcolor=p["card_border"]),
    "divider": lambda p, s: dict(bg=p["divider"]),
    "btn_border": lambda p, s: dict(bg=p["btn_border"]),
    "label": lambda p, s: dict(bg=s, fg=p["text"]),
    "title": lambda p, s: dict(bg=s, fg=p["text"]),
    "hint": lambda p, s: dict(bg=s, fg=p["muted"]),
    "faint": lambda p, s: dict(bg=s, fg=p["faint"]),
    "icon": lambda p, s: dict(bg=s, fg=p["muted"]),
    "error": lambda p, s: dict(bg=s, fg=p["error"]),
    "env_test": lambda p, s: dict(bg=p["test_bg"], fg=p["test"]),
    "env_prod": lambda p, s: dict(bg=p["prod_bg"], fg=p["prod"]),
    "env_none": lambda p, s: dict(bg=s, fg=s),
    "entry": lambda p, s: dict(bg=p["field"], fg=p["text"], insertbackground=p["text"],
                               readonlybackground=p["field"], disabledbackground=s,
                               disabledforeground=p["faint"],
                               selectbackground=p["sel_bg"], selectforeground=p["sel_fg"],
                               relief="flat", bd=0, highlightthickness=1,
                               highlightbackground=p["border"], highlightcolor=p["focus"]),
    "entry_bare": lambda p, s: dict(bg=p["field"], fg=p["text"], insertbackground=p["text"],
                                    selectbackground=p["sel_bg"], selectforeground=p["sel_fg"],
                                    relief="flat", bd=0, highlightthickness=0),
    "field_frame": lambda p, s: dict(bg=p["field"], highlightthickness=1,
                                     highlightbackground=p["border"], highlightcolor=p["focus"]),
    "field_icon": lambda p, s: dict(bg=p["field"], fg=p["muted"]),
    "placeholder": lambda p, s: dict(bg=p["field"], fg=p["faint"]),
    "spinbox": lambda p, s: dict(bg=p["field"], fg=p["text"], insertbackground=p["text"],
                                 buttonbackground=p["btn"], readonlybackground=p["field"],
                                 selectbackground=p["sel_bg"], selectforeground=p["sel_fg"],
                                 relief="flat", bd=0, highlightthickness=1,
                                 highlightbackground=p["border"], highlightcolor=p["focus"]),
    "check": lambda p, s: dict(bg=s, fg=p["text"], activebackground=s,
                               activeforeground=p["text"], selectcolor=p["field"],
                               highlightthickness=0),
    "button": lambda p, s: dict(bg=p["btn"], fg=p["btn_fg"], activebackground=p["btn_hover"],
                                activeforeground=p["btn_fg"], disabledforeground=p["btn_disabled"],
                                highlightthickness=1, highlightbackground=p["btn_border"],
                                highlightcolor=p["focus"], bd=0, relief="flat",
                                overrelief="flat"),
    "primary": lambda p, s: _button(p, "accent"),
    "success": lambda p, s: _button(p, "accent"),
    "danger": lambda p, s: _button(p, "danger"),
    "subtle": lambda p, s: dict(bg=s, fg=p["text"], activebackground=p["subtle_hover"],
                                activeforeground=p["text"], disabledforeground=p["btn_disabled"],
                                highlightthickness=0, bd=0, relief="flat", overrelief="flat"),
    "nav_item": lambda p, s: dict(bg=p["nav"], fg=p["text"]),
    "nav_item_selected": lambda p, s: dict(bg=p["nav_sel"], fg=p["text"]),
    "nav_indicator": lambda p, s: dict(bg=p["nav"]),
    "nav_indicator_selected": lambda p, s: dict(bg=p["accent"]),
    "seg": lambda p, s: dict(bg=p["field"], fg=p["text"], highlightthickness=0),
    "seg_selected": lambda p, s: dict(bg=p["accent"], fg=p["accent_fg"], highlightthickness=0),
    "banner": lambda p, s: dict(bg=p["banner_bg"]),
    "banner_label": lambda p, s: dict(bg=p["banner_bg"], fg=p["banner_fg"]),
    "banner_button": lambda p, s: dict(bg=p["banner_bg"], fg=p["accent"],
                                       activebackground=p["banner_bg"],
                                       activeforeground=p["accent_hover"],
                                       highlightthickness=0, bd=0, relief="flat",
                                       overrelief="flat"),
    "combo": lambda p, s: {},        # ttk: styled by _apply_global, popdown list below
    "tree": lambda p, s: {},
}

# role -> palette key of its colour under the mouse
HOVER = {"button": "btn_hover", "primary": "accent_hover", "success": "accent_hover",
         "danger": "danger_hover", "subtle": "subtle_hover", "nav_item": "subtle_hover",
         "seg": "subtle_hover"}

_state = {"dark": False, "scale": 1.0, "root": None, "icon_font": None}
_widgets: Dict[str, tuple] = {}         # Tk path -> (widget, role, surface)
_toplevels: List = []
_listeners: List[Callable[[], None]] = []


# ----------------------------------------------------------------------
# DPI

def enable_dpi_awareness() -> None:
    """Declare the process DPI aware (system DPI), before any window is
    created: crisp text instead of a stretched 96-DPI bitmap. System-aware
    rather than per-monitor because Tk 8.6 does not handle WM_DPICHANGED:
    on a monitor with another DPI Windows still scales the window for us."""
    try:
        # DPI_AWARENESS_CONTEXT_SYSTEM_AWARE (Windows 10 1607+)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)     # PROCESS_SYSTEM_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError) as e:
            logging.debug(f"DPI awareness not available: {e}")


def px(n: float) -> int:
    """Pixels at 96 DPI -> pixels on this screen."""
    return int(round(n * _state["scale"]))


def scale() -> float:
    return _state["scale"]


# ----------------------------------------------------------------------
# Fonts

def font(size: int = 10, weight: str = "normal"):
    """Segoe UI in both themes; 'semibold' uses the Segoe UI Semibold face."""
    if weight == "semibold":
        return (FONT_SEMIBOLD, size)
    if weight == "bold":
        return (FONT, size, "bold")
    return (FONT, size)


def icon_font(size: int = 10):
    """Font of the Windows icon glyphs (Fluent on 11, MDL2 on 10), or None."""
    return (_state["icon_font"], size) if _state["icon_font"] else None


# ----------------------------------------------------------------------
# Theme

def is_dark() -> bool:
    return _state["dark"]


def palette() -> Dict[str, str]:
    return PALETTES["dark" if _state["dark"] else "light"]


def color(name: str) -> str:
    return palette()[name]


def init(root, dark: bool) -> None:
    """Once, on the Tk root just created: DPI scale, fonts, ttk styles."""
    from tkinter import font as tkfont
    _state["root"] = root
    # 96 when the process is not DPI aware (tests, smoke runs): scale 1.
    _state["scale"] = max(1.0, root.winfo_fpixels("1i") / 96.0)
    _state["dark"] = bool(dark)
    families = set(tkfont.families(root))
    _state["icon_font"] = next((f for f in ICON_FONTS if f in families), None)
    for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkMenuFont", 10),
                       ("TkHeadingFont", 9)):
        try:
            tkfont.nametofont(name, root=root).configure(family=FONT, size=size)
        except Exception:
            pass
    _apply_global(root)
    toplevel(root)


def style(widget, role: str, surface: Optional[str] = None):
    """Register `widget` with `role`, on the surface `surface` (a palette
    key: 'bg', 'card', 'nav'...; default: the one it had), and paint it."""
    prev = _widgets.get(str(widget))
    if surface is None:
        surface = prev[2] if prev else "bg"
    _widgets[str(widget)] = (widget, role, surface)
    _paint(widget, role, surface)
    if role in HOVER and not getattr(widget, "_theme_hover", False):
        widget._theme_hover = True
        widget.bind("<Enter>", lambda e, w=widget: _hover(w, True), add="+")
        widget.bind("<Leave>", lambda e, w=widget: _hover(w, False), add="+")
    return widget


def set_role(widget, role: str):
    return style(widget, role)


def _hover(widget, inside: bool) -> None:
    entry = _widgets.get(str(widget))
    if entry is None:
        return
    _, role, surface = entry
    try:
        if str(widget.cget("state")) == "disabled":
            return
    except Exception:
        pass
    if inside and role in HOVER:
        try:
            widget.configure(bg=color(HOVER[role]))
        except Exception:
            pass
    else:
        _paint(widget, role, surface)


def toplevel(win) -> None:
    """Register a Tk root / Toplevel: background and title bar follow the theme."""
    _toplevels.append(win)
    style(win, "window", "bg")
    _title_bar(win)


def on_change(callback: Callable[[], None]) -> None:
    """`callback()` after every theme switch (e.g. to redraw images whose
    colours depend on the theme)."""
    _listeners.append(callback)


def use(dark: bool) -> None:
    """Switch theme and repaint every registered widget (Tk thread)."""
    dark = bool(dark)
    root = _state["root"]
    if root is None or dark == _state["dark"]:
        _state["dark"] = dark
        return
    _state["dark"] = dark
    _apply_global(root)
    for path, (widget, role, surface) in list(_widgets.items()):
        try:
            if widget.winfo_exists():
                _paint(widget, role, surface)
            else:
                del _widgets[path]
        except Exception as e:
            logging.debug(f"Theme repaint of {path} failed: {e}")
    for win in _toplevels:
        _title_bar(win)
    for cb in list(_listeners):
        try:
            cb()
        except Exception as e:
            logging.error(f"Theme listener failed: {e}", exc_info=True)


def _paint(widget, role: str, surface: str) -> None:
    p = palette()
    opts = ROLES[role](p, p.get(surface, p["bg"]))
    if opts:
        keys = set(widget.keys())
        widget.configure(**{k: v for k, v in opts.items() if k in keys})
    if role == "combo":
        _style_popdown(widget)


def _style_popdown(combo) -> None:
    """Colours of the drop-down list of a ttk.Combobox (a Tk listbox that
    ttk creates once and never restyles)."""
    try:
        pop = combo.tk.call("ttk::combobox::PopdownWindow", combo)
        combo.tk.call(f"{pop}.f.l", "configure",
                      "-background", color("field"), "-foreground", color("text"),
                      "-selectbackground", color("sel_bg"),
                      "-selectforeground", color("sel_fg"), "-font", font(10),
                      "-borderwidth", 0, "-relief", "flat")
    except Exception as e:
        logging.debug(f"Combobox popdown styling failed: {e}")


def _apply_global(root) -> None:
    from tkinter import ttk
    p = palette()
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=p["bg"], foreground=p["text"], fieldbackground=p["field"],
                bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"],
                troughcolor=p["bg"], selectbackground=p["sel_bg"],
                selectforeground=p["sel_fg"], insertcolor=p["text"], arrowcolor=p["muted"],
                font=font(10), focuscolor=p["focus"])
    s.map(".", background=[("disabled", p["bg"])], foreground=[("disabled", p["btn_disabled"])])
    s.configure("TCombobox", fieldbackground=p["field"], background=p["field"],
                foreground=p["text"], arrowcolor=p["muted"], insertcolor=p["text"],
                bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"],
                padding=(px(6), px(4)), arrowsize=px(12))
    s.map("TCombobox", fieldbackground=[("readonly", p["field"])],
          foreground=[("readonly", p["text"])],
          background=[("active", p["field"]), ("readonly", p["field"])],
          selectbackground=[("readonly", p["field"])],
          selectforeground=[("readonly", p["text"])],
          bordercolor=[("focus", p["focus"]), ("active", p["faint"])],
          arrowcolor=[("active", p["text"])])
    # Slim scrollbars without arrows, like Windows 11.
    for orient in ("Vertical", "Horizontal"):
        s.layout(f"{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {
            "sticky": "ns" if orient == "Vertical" else "we",
            "children": [(f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        s.configure(f"{orient}.TScrollbar", background=p["border"], troughcolor=p["field"],
                    bordercolor=p["field"], lightcolor=p["border"], darkcolor=p["border"],
                    arrowsize=px(8), gripcount=0, relief="flat")
        s.map(f"{orient}.TScrollbar", background=[("active", p["faint"])],
              lightcolor=[("active", p["faint"])], darkcolor=[("active", p["faint"])])
    s.configure("Treeview", background=p["field"], fieldbackground=p["field"],
                foreground=p["text"], bordercolor=p["field"], lightcolor=p["field"],
                darkcolor=p["field"], rowheight=px(28), font=font(10), borderwidth=0)
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    s.map("Treeview", background=[("selected", p["sel_bg"])],
          foreground=[("selected", p["sel_fg"])])
    s.configure("Treeview.Heading", background=p["field"], foreground=p["muted"],
                bordercolor=p["field"], lightcolor=p["field"], darkcolor=p["field"],
                relief="flat", font=font(9), padding=(px(6), px(4)))
    s.map("Treeview.Heading", background=[("active", p["subtle_hover"])])
    # Drop-down lists created from now on (the existing ones: _style_popdown).
    for opt, key in (("background", "field"), ("foreground", "text"),
                     ("selectBackground", "sel_bg"), ("selectForeground", "sel_fg")):
        root.option_add(f"*TCombobox*Listbox.{opt}", p[key])


def _title_bar(win) -> None:
    """Dark / light title bar (Windows 10 20H1+ and 11; ignored elsewhere)."""
    try:
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        value = ctypes.c_int(1 if _state["dark"] else 0)
        for attr in (20, 19):       # DWMWA_USE_IMMERSIVE_DARK_MODE (19 before 20H1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception as e:
        logging.debug(f"Title bar theming failed: {e}")
