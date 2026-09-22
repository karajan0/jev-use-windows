"""Small Win32 boundary for a visible, interactive Windows desktop."""

from __future__ import annotations

import ctypes
import sys
import time
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image


@dataclass(frozen=True)
class Window:
    handle: int
    title: str
    rect: tuple[int, int, int, int]


@lru_cache(maxsize=1)
def _user32():
    if sys.platform != "win32":
        raise RuntimeError("An interactive Windows desktop is required")
    api = ctypes.windll.user32
    api.GetForegroundWindow.restype = wintypes.HWND
    api.SetForegroundWindow.argtypes = [wintypes.HWND]
    api.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsIconic.argtypes = [wintypes.HWND]
    api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    api.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetDC.argtypes = [wintypes.HWND]
    api.GetDC.restype = wintypes.HDC
    api.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    api.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    api.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    return api


@lru_cache(maxsize=1)
def _gdi32():
    api = ctypes.windll.gdi32
    api.CreateCompatibleDC.argtypes = [wintypes.HDC]
    api.CreateCompatibleDC.restype = wintypes.HDC
    api.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(_BitmapInfo),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    api.CreateDIBSection.restype = ctypes.c_void_p
    api.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
    api.SelectObject.restype = ctypes.c_void_p
    api.DeleteObject.argtypes = [ctypes.c_void_p]
    api.DeleteDC.argtypes = [wintypes.HDC]
    return api


class _BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("width", wintypes.LONG),
        ("height", wintypes.LONG),
        ("planes", wintypes.WORD),
        ("bit_count", wintypes.WORD),
        ("compression", wintypes.DWORD),
        ("image_size", wintypes.DWORD),
        ("x_pixels_per_meter", wintypes.LONG),
        ("y_pixels_per_meter", wintypes.LONG),
        ("colors_used", wintypes.DWORD),
        ("colors_important", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("header", _BitmapHeader), ("colors", wintypes.DWORD * 3)]


def enable_dpi_awareness() -> None:
    user32 = _user32()
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    user32.SetProcessDPIAware()


def rect_of(handle: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not _user32().GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect)):
        raise RuntimeError("Cannot read the selected window position")
    bounds = (rect.left, rect.top, rect.right, rect.bottom)
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise RuntimeError("The selected window has no visible area")
    return bounds


def capture_window(handle: int, rect: tuple[int, int, int, int]) -> Image.Image:
    """Capture one window through GDI, including a window on a secondary display."""
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width > 6000 or height > 6000:
        raise RuntimeError("The selected window is too large to capture")
    user32, gdi32 = _user32(), _gdi32()
    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise RuntimeError("Could not allocate a window capture buffer")
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            raise RuntimeError("Could not allocate a window capture buffer")
        try:
            info = _BitmapInfo(
                _BitmapHeader(ctypes.sizeof(_BitmapHeader), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
            )
            pixels = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), 0, ctypes.byref(pixels), None, 0)
            if not bitmap or not pixels.value:
                raise RuntimeError("Could not allocate a window capture buffer")
            previous = gdi32.SelectObject(memory_dc, bitmap)
            try:
                if not user32.PrintWindow(wintypes.HWND(handle), memory_dc, 2) and not user32.PrintWindow(
                    wintypes.HWND(handle), memory_dc, 0
                ):
                    raise RuntimeError("The selected window did not render a capture")
                return Image.frombytes(
                    "RGB", (width, height), ctypes.string_at(pixels, width * height * 4), "raw", "BGRX"
                )
            finally:
                gdi32.SelectObject(memory_dc, previous)
                gdi32.DeleteObject(bitmap)
        finally:
            gdi32.DeleteDC(memory_dc)
    finally:
        user32.ReleaseDC(None, screen_dc)


def windows() -> list[Window]:
    user32 = _user32()
    found: list[Window] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _unused):
        if not user32.IsWindowVisible(handle) or user32.IsIconic(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        with suppress(RuntimeError):
            found.append(Window(int(handle), buffer.value, rect_of(int(handle))))
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return found


def monitors() -> list[tuple[int, int, int, int]]:
    found: list[tuple[int, int, int, int]] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, ctypes.c_void_p, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def visit(_monitor, _dc, bounds, _unused):
        item = bounds.contents
        found.append((item.left, item.top, item.right, item.bottom))
        return True

    callback = callback_type(visit)
    _user32().EnumDisplayMonitors(None, None, callback, 0)
    return found


def select_window(query: str | None) -> Window:
    listed = windows()
    if query:
        if query.startswith("#"):
            try:
                handle = int(query[1:], 0)
            except ValueError as exc:
                raise ValueError("Window handle must be # followed by a decimal or 0x hexadecimal number") from exc
            matches = [item for item in listed if item.handle == handle]
            if len(matches) != 1:
                raise ValueError("The selected window handle is not visible")
            return matches[0]
        matches = [item for item in listed if query.casefold() in item.title.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Window query matched {len(matches)} visible windows; use a unique title")
        return matches[0]
    active = int(_user32().GetForegroundWindow())
    for item in listed:
        if item.handle == active:
            return item
    raise RuntimeError("No visible foreground window; use --window")


def activate(handle: int) -> None:
    user32 = _user32()
    if user32.IsIconic(wintypes.HWND(handle)):
        user32.ShowWindow(wintypes.HWND(handle), 9)  # SW_RESTORE
    user32.SetForegroundWindow(wintypes.HWND(handle))
    for _ in range(5):
        if (user32.GetForegroundWindow() or 0) == handle:
            return
        time.sleep(0.02)
    raise RuntimeError("Could not activate the selected window")


_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", _InputUnion)]


def _send(events: list[_Input]) -> None:
    payload = (_Input * len(events))(*events)
    sent = _user32().SendInput(len(events), payload, ctypes.sizeof(_Input))
    if sent != len(events):
        raise RuntimeError(f"Windows accepted {sent} of {len(events)} input events")


def _key(code: int, *, up: bool = False, unicode: bool = False) -> _Input:
    flags = (0x0004 if unicode else 0) | (0x0002 if up else 0)
    return _Input(1, _InputUnion(ki=_KeyboardInput(0 if unicode else code, code if unicode else 0, flags, 0, 0)))


def press(code: int) -> None:
    _send([_key(code), _key(code, up=True)])


def hotkey(modifier: int, key: int) -> None:
    _send([_key(modifier), _key(key), _key(key, up=True), _key(modifier, up=True)])


def type_text(value: str) -> None:
    if not value:
        raise ValueError("Text must not be empty")
    encoded = value.encode("utf-16-le")
    units = [int.from_bytes(encoded[index : index + 2], "little") for index in range(0, len(encoded), 2)]
    for start in range(0, len(units), 128):
        events = [
            event
            for unit in units[start : start + 128]
            for event in (_key(unit, unicode=True), _key(unit, up=True, unicode=True))
        ]
        _send(events)


def click(x: int, y: int) -> None:
    user32 = _user32()
    if not user32.SetCursorPos(x, y):
        raise RuntimeError("Could not move the pointer")
    _send(
        [
            _Input(0, _InputUnion(mi=_MouseInput(0, 0, 0, 0x0002, 0, 0))),
            _Input(0, _InputUnion(mi=_MouseInput(0, 0, 0, 0x0004, 0, 0))),
        ]
    )


def scroll(direction: int) -> None:
    delta = 120 if direction > 0 else -120
    _send([_Input(0, _InputUnion(mi=_MouseInput(0, 0, delta & 0xFFFFFFFF, 0x0800, 0, 0)))])
