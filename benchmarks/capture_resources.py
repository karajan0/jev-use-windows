"""Check GDI resource balance while repeatedly capturing the fixture."""

import ctypes
import json
from ctypes import wintypes

from jev_use import win32


def main():
    win32.enable_dpi_awareness()
    window = win32.select_window("Jev Regression Fixture")
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    user = ctypes.windll.user32
    user.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    process = kernel.GetCurrentProcess()
    win32.capture_region(window.rect).close()
    before = user.GetGuiResources(process, 0)
    for _ in range(100):
        win32.capture_region(window.rect).close()
    after = user.GetGuiResources(process, 0)
    if after != before:
        raise RuntimeError(f"GDI resources changed: {before} -> {after}")
    print(json.dumps({"captures": 100, "gdi_before": before, "gdi_after": after, "balanced": True}))


if __name__ == "__main__":
    main()
