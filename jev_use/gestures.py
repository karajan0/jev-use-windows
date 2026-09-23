"""Explicit bounded gestures with best-effort release on every exit path."""

import time

from . import win32

KEYS = {
    "ctrl": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "space": 0x20,
    "enter": 0x0D,
    "escape": 0x1B,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}


def key_codes(chord):
    codes = []
    for name in chord.casefold().split("+"):
        if name in KEYS:
            code = KEYS[name]
        elif len(name) == 1 and name.isascii() and name.isalnum():
            code = ord(name.upper())
        else:
            raise ValueError(f"Unsupported held key: {name}")
        if code in codes:
            raise ValueError("A chord cannot contain duplicate keys")
        codes.append(code)
    if not 1 <= len(codes) <= 4:
        raise ValueError("Use one to four held keys")
    return codes


def _foreground(handle):
    if win32.select_window(None).handle != handle:
        raise RuntimeError("Foreground changed during gesture; held input was released")


def hold(chord, duration, handle):
    if not 0.01 <= duration <= 2.0:
        raise ValueError("Hold duration must be 10 to 2000 milliseconds")
    codes = key_codes(chord)
    _foreground(handle)
    if any(win32._user32().GetAsyncKeyState(code) & 0x8000 for code in codes):
        raise RuntimeError("A requested key is already held; no gesture was sent")
    pressed = []
    try:
        for code in codes:
            pressed.append(code)
            win32._send([win32._key(code)])
        deadline = time.monotonic() + duration
        while True:
            _foreground(handle)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.02, remaining))
    finally:
        if pressed:
            win32._send([win32._key(code, up=True) for code in reversed(pressed)])


def drag(start, end, duration, handle):
    if not 0.05 <= duration <= 2.0:
        raise ValueError("Drag duration must be between 50 and 2000 milliseconds")
    _foreground(handle)
    api = win32._user32()
    if api.GetAsyncKeyState(1) & 0x8000:
        raise RuntimeError("The left mouse button is already held; no drag was sent")
    if not api.SetCursorPos(*start):
        raise RuntimeError("Could not position the pointer")
    try:
        win32._send([win32._Input(0, win32._InputUnion(mi=win32._MouseInput(0, 0, 0, 0x0002, 0, 0)))])
        steps = max(2, round(duration / 0.016))
        for step in range(1, steps + 1):
            _foreground(handle)
            x = round(start[0] + (end[0] - start[0]) * step / steps)
            y = round(start[1] + (end[1] - start[1]) * step / steps)
            if not api.SetCursorPos(x, y):
                raise RuntimeError("Could not move the pointer during drag")
            time.sleep(duration / steps)
    finally:
        win32._send([win32._Input(0, win32._InputUnion(mi=win32._MouseInput(0, 0, 0, 0x0004, 0, 0)))])
