"""Launcher for BrowserPilot AI FastAPI backend.

Ensures that the backend and all child browser processes (Playwright Chromium)
are bound to the user's interactive desktop (`WinSta0\\Default`) so that
browser navigation is visibly rendered on screen rather than in a background virtual desktop.
"""

import ctypes
import os
from pathlib import Path
import sys


def main():
    # If running on Windows, verify whether we are on the interactive desktop
    if sys.platform == "win32" and "--in-default" not in sys.argv:
        try:
            u32 = ctypes.windll.user32
            k32 = ctypes.windll.kernel32
            buf = ctypes.create_unicode_buffer(256)
            needed = ctypes.c_uint()
            hdesk = u32.GetThreadDesktop(k32.GetCurrentThreadId())
            u32.GetUserObjectInformationW(hdesk, 2, buf, 256, ctypes.byref(needed))
            curr_desktop = buf.value

            if curr_desktop.lower() != "default":
                # Respawn on the user's interactive desktop WinSta0\Default
                from ctypes import wintypes

                class STARTUPINFOW(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("lpReserved", wintypes.LPWSTR),
                        ("lpDesktop", wintypes.LPWSTR),
                        ("lpTitle", wintypes.LPWSTR),
                        ("dwX", wintypes.DWORD),
                        ("dwY", wintypes.DWORD),
                        ("dwXSize", wintypes.DWORD),
                        ("dwYSize", wintypes.DWORD),
                        ("dwXCountChars", wintypes.DWORD),
                        ("dwYCountChars", wintypes.DWORD),
                        ("dwFillAttribute", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD),
                        ("wShowWindow", wintypes.WORD),
                        ("cbReserved2", wintypes.WORD),
                        ("lpReserved2", ctypes.c_char_p),
                        ("hStdInput", wintypes.HANDLE),
                        ("hStdOutput", wintypes.HANDLE),
                        ("hStdError", wintypes.HANDLE),
                    ]

                class PROCESS_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ("hProcess", wintypes.HANDLE),
                        ("hThread", wintypes.HANDLE),
                        ("dwProcessId", wintypes.DWORD),
                        ("dwThreadId", wintypes.DWORD),
                    ]

                si = STARTUPINFOW()
                si.cb = ctypes.sizeof(STARTUPINFOW)
                si.lpDesktop = "WinSta0\\Default"

                pi = PROCESS_INFORMATION()

                # Build command line forwarding all arguments
                script_path = Path(__file__).resolve()
                cmd = f'"{sys.executable}" "{script_path}" --in-default {" ".join(sys.argv[1:])}'.strip()

                success = k32.CreateProcessW(
                    None,
                    cmd,
                    None,
                    None,
                    False,
                    0,
                    None,
                    None,
                    ctypes.byref(si),
                    ctypes.byref(pi),
                )
                if success:
                    # Wait for child process so runner tracks it cleanly
                    k32.WaitForSingleObject(pi.hProcess, 0xFFFFFFFF)
                    k32.CloseHandle(pi.hProcess)
                    k32.CloseHandle(pi.hThread)
                    return
        except Exception:
            pass

    # Ensure project root is in sys.path
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # Run uvicorn server directly
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
