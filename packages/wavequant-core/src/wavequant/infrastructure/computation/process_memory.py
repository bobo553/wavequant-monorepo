"""只读取调用者或其已知计算子进程的内存峰值。"""

import os
from pathlib import Path
import sys


def peak_rss_bytes(pid: int | None = None) -> int | None:
    process_id = os.getpid() if pid is None else pid
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD),
                        ('peak_working_set', ctypes.c_size_t), ('working_set', ctypes.c_size_t),
                        ('peak_paged', ctypes.c_size_t), ('paged', ctypes.c_size_t),
                        ('peak_nonpaged', ctypes.c_size_t), ('nonpaged', ctypes.c_size_t),
                        ('pagefile', ctypes.c_size_t), ('peak_pagefile', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, process_id)
        if not handle:
            return None
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.peak_working_set)
        finally:
            kernel.CloseHandle(handle)
    if process_id != os.getpid():
        try:
            for line in Path(f'/proc/{process_id}/status').read_text(encoding='ascii').splitlines():
                if line.startswith('VmHWM:'):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            return None
        return None
    import resource
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * (1 if sys.platform == 'darwin' else 1024)
