"""Windows OS read APIs only. No game calls, writes, injection, suspension or input."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from memory import Refused, checked_range, PinnedImage


class WindowsMemory:
    def __init__(self, pid):
        if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8: raise Refused('Live reader requires 64-bit Windows Python')
        if not isinstance(pid,int) or pid <= 0: raise Refused('Explicit positive process ID required')
        self.api = ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; self.api.OpenProcess.restype=wintypes.HANDLE
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]; self.api.CloseHandle.restype=wintypes.BOOL
        self.api.ReadProcessMemory.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
        self.api.ReadProcessMemory.restype=wintypes.BOOL
        self.api.VirtualQueryEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t]; self.api.VirtualQueryEx.restype=ctypes.c_size_t
        self.api.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        self.api.QueryFullProcessImageNameW.restype=wintypes.BOOL
        # PROCESS_VM_READ | PROCESS_QUERY_INFORMATION. No write/operation/thread rights.
        self.handle = self.api.OpenProcess(0x0010 | 0x0400,False,pid)
        if not self.handle: raise Refused(f'OpenProcess read access failed: Win32 {ctypes.get_last_error()}')
        try:
            path = ctypes.create_unicode_buffer(32768); size=wintypes.DWORD(len(path))
            if not self.api.QueryFullProcessImageNameW(self.handle,0,path,ctypes.byref(size)): raise Refused('Cannot identify selected executable')
            self.image = PinnedImage(path.value)
            self.base,self.module_size = self._module(pid,path.value)
            if self.module_size != self.image.image_size: raise Refused('Loaded image size differs from pinned executable')
            # Loaded image must agree with the pinned file at concrete root/decoder/setter seams.
            for rva in (0x9E43A0,0xD6FD10,0x99F8E0,0x976720,0xA235C0,0x50B530,0xC3F860,0xA3A410,0x21CCB0,0x21CD30):
                if self.read(self.base+rva,32) != self.image.code(rva): raise Refused(f'Loaded code prefix differs at RVA {rva:#x}')
            self.pid=pid
        except BaseException:
            self.close(); raise

    def _module(self,pid,path):
        class Module(ctypes.Structure):
            _fields_=[('dwSize',wintypes.DWORD),('th32ModuleID',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),
                      ('GlblcntUsage',wintypes.DWORD),('ProccntUsage',wintypes.DWORD),('modBaseAddr',ctypes.c_void_p),
                      ('modBaseSize',wintypes.DWORD),('hModule',wintypes.HMODULE),('szModule',wintypes.WCHAR*256),('szExePath',wintypes.WCHAR*260)]
        self.api.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]; self.api.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
        self.api.Module32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Module)]; self.api.Module32FirstW.restype=wintypes.BOOL
        self.api.Module32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Module)]; self.api.Module32NextW.restype=wintypes.BOOL
        snapshot=self.api.CreateToolhelp32Snapshot(0x8|0x10,pid)
        if snapshot == ctypes.c_void_p(-1).value: raise Refused('Cannot inspect selected process modules')
        try:
            entry=Module(); entry.dwSize=ctypes.sizeof(entry)
            found=self.api.Module32FirstW(snapshot,ctypes.byref(entry))
            for _ in range(4096):
                if not found: break
                if os.path.normcase(entry.szExePath) == os.path.normcase(path): return entry.modBaseAddr,entry.modBaseSize
                found=self.api.Module32NextW(snapshot,ctypes.byref(entry))
        finally: self.api.CloseHandle(snapshot)
        raise Refused('Selected process executable not found in module inventory')

    def read(self,address,size):
        checked_range(address,size)
        class Region(ctypes.Structure):
            _fields_=[('BaseAddress',ctypes.c_void_p),('AllocationBase',ctypes.c_void_p),('AllocationProtect',wintypes.DWORD),
                      ('PartitionId',wintypes.WORD),('RegionSize',ctypes.c_size_t),('State',wintypes.DWORD),('Protect',wintypes.DWORD),('Type',wintypes.DWORD)]
        cursor=address
        while cursor < address+size:
            region=Region()
            if self.api.VirtualQueryEx(self.handle,ctypes.c_void_p(cursor),ctypes.byref(region),ctypes.sizeof(region)) != ctypes.sizeof(region): raise Refused('Unreadable process memory region')
            # MEM_COMMIT plus explicit readable protections; reject guard/noaccess and execute-only.
            if region.State != 0x1000 or region.Protect & (0x100|1) or (region.Protect & 0xFF) not in (2,4,8,0x20,0x40,0x80):
                raise Refused('Memory is uncommitted, guarded or unreadable')
            end=(region.BaseAddress or 0)+region.RegionSize
            if end <= cursor: raise Refused('Invalid memory region size')
            cursor=min(end,address+size)
        buffer=ctypes.create_string_buffer(size); actual=ctypes.c_size_t()
        if not self.api.ReadProcessMemory(self.handle,ctypes.c_void_p(address),buffer,size,ctypes.byref(actual)) or actual.value != size:
            raise Refused(f'Failed/partial process read: Win32 {ctypes.get_last_error()}')
        return buffer.raw

    def close(self):
        if self.handle: self.api.CloseHandle(self.handle); self.handle=None

    def __enter__(self): return self
    def __exit__(self,*_): self.close()
