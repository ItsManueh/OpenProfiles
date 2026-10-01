"""
Frameless windows that keep the native Windows behavior: the shadow, the rounded
corners of Windows 11 and, for the main window, resizing from the edges, Aero
Snap, the minimize and maximize animations, and minimizing from the taskbar.

The window keeps the styles of a normal window (caption and, when resizable,
resizable frame, minimize and maximize boxes) and answers WM_NCCALCSIZE so that
its client area covers the whole window; the app then draws its own title bar.
Dragging uses QWindow.startSystemMove(), which runs the native move loop (and so
Snap). Dialogs use the same with a fixed size.

bring_to_front() puts a window in front of the others even when another app is
active, which Windows does not allow by default.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

log = logging.getLogger("app.gui")

RESIZE_BORDER = 6  # logical pixels along the edges that resize the window

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    WM_NCCALCSIZE = 0x0083
    WM_NCHITTEST = 0x0084
    GWL_STYLE = -16
    WS_CAPTION, WS_SYSMENU = 0x00C00000, 0x00080000
    WS_THICKFRAME, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00040000, 0x00020000, 0x00010000
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0001, 0x0002, 0x0004, 0x0010, 0x0020
    SWP_REFRESH_FRAME = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SW_RESTORE = 9
    SM_CXSIZEFRAME = 32
    SM_CXPADDEDBORDER = 92
    DWMWA_WINDOW_CORNER_PREFERENCE = 33
    DWMWA_BORDER_COLOR = 34
    DWMWCP_ROUND = 2
    HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
    HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

    class NCCALCSIZE_PARAMS(ctypes.Structure):  # noqa: N801 (Windows name)
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    dwmapi = ctypes.windll.dwmapi
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetFocus.argtypes = [wintypes.HWND]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = ctypes.c_uint
    user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, ctypes.c_uint]
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    def _set_dword_attribute(hwnd: int, attribute: int, value: int) -> None:
        data = wintypes.DWORD(value)
        # Fails harmlessly on Windows 10, which does not have these attributes.
        dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))


def make_frameless(window: QWidget, *, resizable: bool = True) -> None:
    """Hides the native title bar. Call once, before the window is shown. A window that is
    not resizable (a dialog) cannot be resized, maximized or minimized either."""
    window.setWindowFlags(window.windowFlags() | Qt.WindowType.FramelessWindowHint)
    if sys.platform != "win32":
        return
    hwnd = int(window.winId())  # creates the native window, after the flags are final
    styles = WS_CAPTION | WS_SYSMENU  # the caption style keeps the shadow and the rounded corners
    if resizable:
        styles |= WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    user32.SetWindowLongW(hwnd, GWL_STYLE, style | styles)
    _set_dword_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_REFRESH_FRAME)


def set_border_color(window: QWidget, color: QColor) -> None:
    """Color of the thin border that Windows 11 draws around the window."""
    if sys.platform == "win32":
        _set_dword_attribute(
            int(window.winId()), DWMWA_BORDER_COLOR, color.red() | color.green() << 8 | color.blue() << 16
        )


def handle_message(window: QWidget, address: int, *, resizable: bool = True) -> int | None:
    """Answers the native messages that keep the window frameless; None lets Qt handle it."""
    if sys.platform != "win32":
        return None
    msg = wintypes.MSG.from_address(address)
    if msg.message == WM_NCCALCSIZE and msg.wParam:
        if user32.IsZoomed(msg.hWnd):
            # Maximized windows overflow the screen by their frame; keep the content inside it.
            dpi = user32.GetDpiForWindow(msg.hWnd)
            inset = user32.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi) + user32.GetSystemMetricsForDpi(
                SM_CXPADDEDBORDER, dpi
            )
            rect = NCCALCSIZE_PARAMS.from_address(msg.lParam).rgrc[0]
            rect.left += inset
            rect.top += inset
            rect.right -= inset
            rect.bottom -= inset
        return 0  # the client area is the whole window: no native title bar or borders
    if msg.message == WM_NCHITTEST and resizable and not user32.IsZoomed(msg.hWnd):
        x = ctypes.c_short(msg.lParam & 0xFFFF).value  # screen coordinates, signed
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        rect = wintypes.RECT()
        user32.GetWindowRect(msg.hWnd, ctypes.byref(rect))
        border = round(RESIZE_BORDER * window.devicePixelRatioF())
        left, right = x < rect.left + border, x >= rect.right - border
        top, bottom = y < rect.top + border, y >= rect.bottom - border
        if top:
            return HTTOPLEFT if left else HTTOPRIGHT if right else HTTOP
        if bottom:
            return HTBOTTOMLEFT if left else HTBOTTOMRIGHT if right else HTBOTTOM
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
    return None


def bring_to_front(window: QWidget) -> None:
    """Shows the window in front of every other one and gives it the keyboard focus.

    Windows only lets the app the user is using take the foreground. Opening the .exe
    starts the app in a second process after a few seconds, so by then another window
    may be active and the app would open behind it. Joining the input of the active
    window for a moment lets this window take the foreground anyway."""
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()
    if sys.platform != "win32":
        return
    hwnd = int(window.winId())
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    foreground = user32.GetForegroundWindow()
    if foreground == hwnd:
        return
    this_thread = kernel32.GetCurrentThreadId()
    other_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(
        other_thread and other_thread != this_thread and user32.AttachThreadInput(this_thread, other_thread, True)
    )
    try:
        # Briefly "always on top" so it shows above the others even if the focus is refused.
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(this_thread, other_thread, False)
    if user32.GetForegroundWindow() != hwnd:
        log.debug("Windows did not let the window take the foreground")
