"""Liquid-Glass blur effect — macOS NSVisualEffectView + Windows DWM Mica/Acrylic.

Call ``apply_glass_effect(window)`` AFTER ``window.show()``.
Every platform call is wrapped in a bare ``except Exception: pass`` so the app
never crashes when a native API is unavailable or the OS version is too old.

macOS: inserts an NSVisualEffectView behind the Qt content view and makes the
       window transparent so the OS blur is visible through semi-opaque panels.
Windows 11: Mica effect via DwmSetWindowAttribute(DWMWA_MICA_EFFECT).
Windows 10: Acrylic blur via SetWindowCompositionAttribute.
"""

from __future__ import annotations

import sys
from typing import Any


def apply_glass_effect(window: Any) -> None:
    """Apply platform-appropriate translucency / blur to *window*."""
    if sys.platform == "darwin":
        _macos_vibrancy(window)
    elif sys.platform == "win32":
        _windows_acrylic(window)


# ── macOS ──────────────────────────────────────────────────────────────────────


def _macos_vibrancy(window: Any) -> None:
    """Insert an NSVisualEffectView behind Qt's content view on macOS."""
    try:
        import ctypes
        import ctypes.util
        from PyQt6.QtCore import Qt  # noqa: PLC0415

        lib = ctypes.CDLL(ctypes.util.find_library("objc") or "libobjc.dylib")
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]

        def _sel(n: str) -> ctypes.c_void_p:
            return lib.sel_registerName(n.encode())  # type: ignore[return-value]

        def _cls(n: str) -> ctypes.c_void_p:
            return lib.objc_getClass(n.encode())  # type: ignore[return-value]

        def _msg(obj: Any, sel_name: str, *args: Any) -> Any:
            f = lib.objc_msgSend
            f.restype = ctypes.c_void_p
            f.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_void_p] * len(args)
            return f(obj, _sel(sel_name), *args)

        def _msg_long(obj: Any, sel_name: str, val: int) -> None:
            f = lib.objc_msgSend
            f.restype = None
            f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
            f(obj, _sel(sel_name), val)

        def _msg_ulong(obj: Any, sel_name: str, val: int) -> None:
            f = lib.objc_msgSend
            f.restype = None
            f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            f(obj, _sel(sel_name), val)

        def _msg_bool(obj: Any, sel_name: str, val: bool) -> None:
            f = lib.objc_msgSend
            f.restype = None
            f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            f(obj, _sel(sel_name), val)

        def _msg_ptr(obj: Any, sel_name: str, val: Any) -> None:
            f = lib.objc_msgSend
            f.restype = None
            f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            f(obj, _sel(sel_name), val)

        # winId() → NSView* → NSWindow* → contentView
        ns_view   = int(window.winId())
        ns_window = _msg(ns_view, "window")
        content   = _msg(ns_window, "contentView")

        # Create and configure NSVisualEffectView
        vev = _msg(_msg(_cls("NSVisualEffectView"), "alloc"), "init")
        _msg_long(vev, "setMaterial:", 18)    # underWindowBackground — adapts dark/light
        _msg_long(vev, "setBlendingMode:", 0)  # behindWindow
        _msg_long(vev, "setState:", 1)         # active

        # NSViewWidthSizable(2) | NSViewHeightSizable(16) = 18
        _msg_ulong(vev, "setAutoresizingMask:", 18)

        # addSubview:positioned:relativeTo: — NSWindowBelow = 0
        f_add = lib.objc_msgSend
        f_add.restype = None
        f_add.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p,
        ]
        f_add(content, _sel("addSubview:positioned:relativeTo:"), vev, 0, None)

        # Make the NSWindow transparent so Qt can composite over the blur
        clear = _msg(_cls("NSColor"), "clearColor")
        _msg_ptr(ns_window, "setBackgroundColor:", clear)
        _msg_bool(ns_window, "setOpaque:", False)

        # Tell Qt to render with alpha so semi-opaque widgets reveal the blur
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    except Exception:
        pass


# ── Windows ────────────────────────────────────────────────────────────────────


def _windows_acrylic(window: Any) -> None:
    """Enable Mica (Win 11) or Acrylic (Win 10) blur-behind the window."""
    try:
        import ctypes
        import ctypes.wintypes  # noqa: F401
        from PyQt6.QtCore import Qt  # noqa: PLC0415

        hwnd = int(window.winId())

        # Windows 11: Mica (DWMWA_MICA_EFFECT = 1029)
        try:
            dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
            value = ctypes.c_int(1)
            if (
                dwmapi.DwmSetWindowAttribute(
                    hwnd, 1029, ctypes.byref(value), ctypes.sizeof(value)
                )
                == 0
            ):
                window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                return
        except Exception:
            pass

        # Windows 10: Acrylic via SetWindowCompositionAttribute
        class _Accent(ctypes.Structure):
            _fields_ = [
                ("AccentState",   ctypes.c_uint),
                ("AccentFlags",   ctypes.c_uint),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId",   ctypes.c_uint),
            ]

        class _WCA(ctypes.Structure):
            _fields_ = [
                ("Attribute",  ctypes.c_uint),
                ("Data",       ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        accent = _Accent()
        accent.AccentState   = 4          # ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.GradientColor = 0x18000000  # near-transparent black tint

        wca = _WCA()
        wca.Attribute  = 19  # WCA_ACCENT_POLICY
        wca.Data       = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        wca.SizeOfData = ctypes.sizeof(accent)

        ctypes.windll.user32.SetWindowCompositionAttribute(  # type: ignore[attr-defined]
            hwnd, ctypes.byref(wca)
        )
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    except Exception:
        pass
