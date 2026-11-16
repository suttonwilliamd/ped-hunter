"""Run a command on a separate, never-displayed Windows desktop.

Windows created by the child live on that desktop: nothing appears on the
user's screen, nothing takes focus and no input is sent anywhere.
usage: python hidden_run.py <timeout-seconds> <exe> [args...]
"""
import ctypes, subprocess, sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


user32.CreateDesktopW.restype = wintypes.HANDLE
user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD,
                                  wintypes.DWORD, ctypes.c_void_p]
kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                    ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION)]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

timeout = int(float(sys.argv[1]) * 1000)
name = "ped-hunter-perf-hidden"
desktop = user32.CreateDesktopW(name, None, None, 0, 0x10000000, None)  # GENERIC_ALL
if not desktop:
    raise ctypes.WinError(ctypes.get_last_error())
startup = STARTUPINFO()
startup.cb = ctypes.sizeof(startup)
startup.lpDesktop = name
info = PROCESS_INFORMATION()
command = ctypes.create_unicode_buffer(subprocess.list2cmdline(sys.argv[2:]))
CREATE_NO_WINDOW = 0x08000000
if not kernel32.CreateProcessW(None, command, None, None, False, CREATE_NO_WINDOW, None, None,
                               ctypes.byref(startup), ctypes.byref(info)):
    raise ctypes.WinError(ctypes.get_last_error())
if kernel32.WaitForSingleObject(info.hProcess, timeout) != 0:
    kernel32.TerminateProcess(info.hProcess, 124)
    print("TIMEOUT: child terminated")
code = wintypes.DWORD()
kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
user32.CloseDesktop(desktop)
sys.exit(code.value)
