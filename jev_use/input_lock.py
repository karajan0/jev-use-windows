"""Serialize Jev input within a Windows logon session."""

import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from functools import wraps


class InputBusyError(RuntimeError):
    pass


@contextmanager
def input_lock(name="Local\\JevComputerUse.Input"):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = [wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        outcome = api.WaitForSingleObject(handle, 0)
        if outcome == 0x102:  # WAIT_TIMEOUT
            raise InputBusyError("Another Jev task is controlling this Windows session")
        if outcome not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


def exclusive(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            with input_lock():
                return function(*args, **kwargs)
        except InputBusyError as exc:
            return {"status": "blocked", "error": str(exc), "actions": []}

    return wrapped
