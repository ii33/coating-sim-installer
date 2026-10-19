"""Create and verify a Unicode .lnk with the Windows Shell Link COM API."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from uuid import UUID


class GUID(ctypes.Structure):
    _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str) -> GUID:
        return cls.from_buffer_copy(UUID(value).bytes_le)


def _method(pointer, index, *arguments):
    address = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[index]
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arguments)(address)


def _checked(result: int, operation: str) -> None:
    if result < 0:
        raise OSError(f"Windows Shell Link {operation} failed (HRESULT 0x{result & 0xffffffff:08x})")


def create_and_verify(path: str, target: str, arguments: str, directory: str, icon: str) -> None:
    """Create a new shortcut, then read its exact fields without resolving it."""
    if not hasattr(ctypes, "WinDLL"):
        raise OSError("Windows Shell Link COM is unavailable")
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    initialized = ole.CoInitializeEx(None, 2)  # STA; do not alter process policy.
    if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE
        _checked(initialized, "COM initialization")
    should_uninitialize = initialized >= 0
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        clsid = GUID.parse("00021401-0000-0000-C000-000000000046")
        link_iid = GUID.parse("000214F9-0000-0000-C000-000000000046")
        persist_iid = GUID.parse("0000010B-0000-0000-C000-000000000046")
        _checked(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(link_iid),
                                      ctypes.byref(link)), "creation")
        for index, value, label in ((20, target, "target"), (11, arguments, "arguments"),
                                    (9, directory, "working directory"), (7, "Coating Simulator", "description")):
            _checked(_method(link, index, wintypes.LPCWSTR)(link, value), label)
        _checked(_method(link, 17, wintypes.LPCWSTR, ctypes.c_int)(link, icon, 0), "icon")
        _checked(_method(link, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            link, ctypes.byref(persist_iid), ctypes.byref(persist)), "persistence interface")
        _checked(_method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(persist, path, True), "save")
        _checked(_method(persist, 5, wintypes.LPCWSTR, wintypes.DWORD)(persist, path, 0), "readback")
        size = 32768
        actual = []
        for index, label in ((3, "target"), (10, "arguments"), (8, "working directory")):
            value = ctypes.create_unicode_buffer(size)
            if index == 3:
                result = _method(link, index, wintypes.LPWSTR, ctypes.c_int,
                                 ctypes.c_void_p, wintypes.DWORD)(link, value, size, None, 4)
            else:
                result = _method(link, index, wintypes.LPWSTR, ctypes.c_int)(link, value, size)
            _checked(result, label + " readback")
            actual.append(value.value)
        icon_value = ctypes.create_unicode_buffer(size)
        icon_index = ctypes.c_int()
        _checked(_method(link, 16, wintypes.LPWSTR, ctypes.c_int, ctypes.POINTER(ctypes.c_int))(
            link, icon_value, size, ctypes.byref(icon_index)), "icon readback")
        if (actual[0].casefold() != target.casefold() or actual[1] != arguments
                or actual[2].casefold() != directory.casefold()
                or icon_value.value.casefold() != icon.casefold() or icon_index.value != 0):
            raise OSError("Windows Shell Link readback differs from the selected project")
    finally:
        if persist.value:
            _method(persist, 2)(persist)
        if link.value:
            _method(link, 2)(link)
        if should_uninitialize:
            ole.CoUninitialize()
