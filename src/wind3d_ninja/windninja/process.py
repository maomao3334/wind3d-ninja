from __future__ import annotations

import ctypes
from ctypes import wintypes
import ntpath
import os
import subprocess
import sys
from threading import RLock


_SPAWN_LOCK = RLock()


def _windows_dll_api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetDllDirectoryW.argtypes = (wintypes.DWORD, wintypes.LPWSTR)
    api.GetDllDirectoryW.restype = wintypes.DWORD
    api.SetDllDirectoryW.argtypes = (wintypes.LPCWSTR,)
    api.SetDllDirectoryW.restype = wintypes.BOOL
    return api


def _dll_directory(api):
    capacity = api.GetDllDirectoryW(0, None)
    if not capacity:
        return None
    while True:
        buffer = ctypes.create_unicode_buffer(capacity + 1)
        length = api.GetDllDirectoryW(len(buffer), buffer)
        if length < len(buffer):
            return buffer.value
        capacity = length


def _child_environment(environment):
    result = dict(os.environ if environment is None else environment)
    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return result
    bundle = ntpath.normcase(ntpath.abspath(str(bundle)))

    def bundled(entry):
        if not entry.strip():
            return False
        candidate = ntpath.normcase(ntpath.abspath(ntpath.expandvars(entry.strip().strip('"'))))
        try:
            return ntpath.commonpath((bundle, candidate)) == bundle
        except ValueError:
            return False

    for name in tuple(result):
        if name.casefold() == "path":
            result[name] = ";".join(entry for entry in result[name].split(";") if not bundled(entry))
        elif name.upper().startswith(("QT_", "GDAL_", "PROJ_")) and bundled(result[name]):
            del result[name]
    return result


def external_popen(*args, **kwargs):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return subprocess.Popen(*args, **kwargs)
    if len(args) > 10:
        positional = list(args)
        positional[10] = _child_environment(positional[10])
        args = tuple(positional)
    else:
        kwargs["env"] = _child_environment(kwargs.get("env"))
    with _SPAWN_LOCK:
        api = _windows_dll_api()
        original = _dll_directory(api)
        try:
            if not api.SetDllDirectoryW(None):
                raise OSError("SetDllDirectoryW failed while preparing the external process")
            return subprocess.Popen(*args, **kwargs)
        finally:
            if not api.SetDllDirectoryW(original):
                raise OSError("SetDllDirectoryW failed while restoring the application DLL directory")
