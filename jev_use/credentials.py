"""Load a Jev key from the process or a Windows account-protected file."""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Provider:
    key: str
    base_url: str | None = None
    model: str | None = None


class _Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _decrypt(blob: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI keys require Windows")
    encrypted = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    source = _Blob(len(blob), encrypted)
    result = _Blob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise RuntimeError("Cannot decrypt the saved key under this Windows account")
    try:
        return ctypes.string_at(result.data, result.length)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(result.data)


def load(root: Path) -> Provider:
    direct = os.environ.get("TYPESAFE_API_KEY")
    gateway = os.environ.get("AI_GATEWAY_API_KEY")
    if direct:
        return Provider(direct)
    if gateway:
        return Provider(gateway, "https://ai-gateway.vercel.sh/typesafe", "typesafe-ai/jev")
    config = root / "config"
    try:
        kind = json.loads((config / "provider.json").read_text(encoding="utf-8"))["provider"]
        if kind not in {"typesafe", "vercel"}:
            raise ValueError("Unknown saved key provider")
        key = _decrypt((config / "key.dpapi").read_bytes()).decode("utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError("No API key. Run Set-Key.ps1 or set TYPESAFE_API_KEY or AI_GATEWAY_API_KEY") from exc
    if len(key) < 20:
        raise RuntimeError("The saved API key is empty or invalid")
    return (
        Provider(key)
        if kind == "typesafe"
        else Provider(key, "https://ai-gateway.vercel.sh/typesafe", "typesafe-ai/jev")
    )
