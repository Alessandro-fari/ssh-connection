"""
Native colored bitmaps for Win32 menu items.

Windows draws menu text (and emoji) monochrome, so status glyphs like a
green dot cannot be rendered via text. Menus do support a real per-item
bitmap (MENUITEMINFO.hbmpItem) with alpha — pystray does not expose it,
so this module decorates the HMENU that pystray builds.

Visual language:
  TEST  → circles:  cold-white outline (idle) / solid green (connected)
  PROD  → squares:  warm-amber outline (idle) / solid green (connected)
"""

import ctypes
import ctypes.wintypes as wt
import logging

from PIL import Image, ImageDraw

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# 64-bit handles get truncated with the default c_int restype
gdi32.CreateDIBSection.restype = wt.HBITMAP
user32.GetSubMenu.restype = wt.HMENU
user32.CreatePopupMenu.restype = wt.HMENU

MIIM_BITMAP = 0x00000080
BI_RGB = 0
SM_CXMENUCHECK = 71

GREEN = (46, 160, 67, 255)          # connected (both environments)
GREEN_EDGE = (24, 110, 44, 255)
TEST_WHITE = (245, 246, 250, 255)   # cold white for TEST
TEST_EDGE = (150, 155, 165, 255)
PROD_WHITE = (255, 244, 214, 255)   # warm white for PROD
PROD_EDGE = (227, 179, 65, 255)     # amber edge: production = caution


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class _MENUITEMINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("fMask", wt.UINT), ("fType", wt.UINT),
        ("fState", wt.UINT), ("wID", wt.UINT), ("hSubMenu", wt.HMENU),
        ("hbmpChecked", wt.HBITMAP), ("hbmpUnchecked", wt.HBITMAP),
        ("dwItemData", ctypes.c_void_p), ("dwTypeData", wt.LPWSTR),
        ("cch", wt.UINT), ("hbmpItem", wt.HBITMAP),
    ]


class MenuBitmaps:
    """Cache of HBITMAPs keyed by status, plus HMENU decoration."""

    def __init__(self):
        self._bitmaps = {}
        self._size = max(16, user32.GetSystemMetrics(SM_CXMENUCHECK))

    # ------------------------------------------------------------------
    # Bitmap generation

    def _draw(self, key: str) -> Image.Image:
        s = self._size
        scale = 4  # draw oversampled, downscale for smooth edges
        big = s * scale
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        m = big // 6  # margin
        box = [m, m, big - m, big - m]
        w = max(scale, big // 10)

        if key == "test_idle":
            d.ellipse(box, fill=TEST_WHITE, outline=TEST_EDGE, width=w)
        elif key == "test_active":
            d.ellipse(box, fill=GREEN, outline=GREEN_EDGE, width=w)
        elif key == "prod_idle":
            d.rectangle(box, fill=PROD_WHITE, outline=PROD_EDGE, width=w)
        elif key == "prod_active":
            d.rectangle(box, fill=GREEN, outline=GREEN_EDGE, width=w)

        return img.resize((s, s), Image.LANCZOS)

    def _to_hbitmap(self, img: Image.Image):
        """Create a 32-bit premultiplied-alpha DIB section from a PIL image."""
        w, h = img.size
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        if not hbm or not bits:
            return None

        # Menu bitmaps require premultiplied BGRA
        rgba = img.tobytes()
        buf = bytearray(w * h * 4)
        for i in range(w * h):
            r, g, b, a = rgba[i * 4:i * 4 + 4]
            buf[i * 4:i * 4 + 4] = bytes((b * a // 255, g * a // 255, r * a // 255, a))
        ctypes.memmove(bits, bytes(buf), len(buf))
        return hbm

    def get(self, key: str):
        if key not in self._bitmaps:
            try:
                self._bitmaps[key] = self._to_hbitmap(self._draw(key))
            except Exception as e:
                logging.warning(f"Menu bitmap creation failed for {key}: {e}")
                self._bitmaps[key] = None
        return self._bitmaps[key]

    # ------------------------------------------------------------------
    # HMENU decoration

    def _set_item_bitmap(self, hmenu, position: int, key: str) -> None:
        hbm = self.get(key)
        if not hbm:
            return
        mii = _MENUITEMINFO()
        mii.cbSize = ctypes.sizeof(_MENUITEMINFO)
        mii.fMask = MIIM_BITMAP
        mii.hbmpItem = hbm
        user32.SetMenuItemInfoW(hmenu, position, True, ctypes.byref(mii))

    def apply_plan(self, hmenu, plan) -> None:
        """
        Decorate `hmenu` according to `plan`: a list of
        (top_index, key_or_None, child_keys_or_None); child keys apply to
        the submenu at that position (None entries are skipped).
        """
        for top_index, key, children in plan:
            if key:
                self._set_item_bitmap(hmenu, top_index, key)
            if children:
                sub = user32.GetSubMenu(hmenu, top_index)
                if sub:
                    for child_index, child_key in enumerate(children):
                        if child_key:
                            self._set_item_bitmap(sub, child_index, child_key)
