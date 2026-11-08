from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)

_JOB = None
_OWN_GROUP = False


def stdin_handle(stream):
    import msvcrt
    return msvcrt.get_osfhandle(stream.fileno())


def pipe_has_data(handle) -> bool | None:
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    available = wintypes.DWORD()
    if not k.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
        return None
    return available.value > 0


def release_stdin() -> None:
    if not IS_WINDOWS:
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    ctypes.windll.kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    ctypes.windll.kernel32.SetStdHandle(wintypes.DWORD(-10 & 0xFFFFFFFF),
                                        msvcrt.get_osfhandle(sys.stdin.fileno()))


def contain_children(low_priority: bool) -> None:
    global _JOB, _OWN_GROUP
    if not IS_WINDOWS:
        try:
            os.setpgrp()
            _OWN_GROUP = os.getpgrp() == os.getpid()
        except OSError:
            _OWN_GROUP = False
        if _OWN_GROUP:
            import atexit
            atexit.register(kill_children)
        if low_priority:
            try:
                os.nice(10)
            except OSError:
                pass
        return
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    if low_priority:
        k.SetPriorityClass(wintypes.HANDLE(-1), 0x4000)

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k.CreateJobObjectW(None, None)
    if not job:
        return
    info = Extended()
    # Downloads stay contained; only explicit shell actions may break away.
    info.Basic.LimitFlags = 0x2000 | 0x0800  # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
    if k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) and \
            k.AssignProcessToJobObject(job, wintypes.HANDLE(-1)):
        _JOB = job


def kill_children() -> None:
    if IS_WINDOWS or not _OWN_GROUP:
        return
    import signal
    try:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.killpg(os.getpgrp(), signal.SIGTERM)
    except OSError:
        pass


def job_usage() -> dict:
    if IS_WINDOWS:
        return _job_usage_windows() if _JOB else {}
    if not _OWN_GROUP:
        return {}
    import resource
    own, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    peak = max(own.ru_maxrss, kids.ru_maxrss)
    return {"cpu_s": round(own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime, 2),
            "peak_mb": round(peak / (1048576 if IS_MAC else 1024), 1)}


def _job_usage_windows() -> dict:
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.c_void_p]

    class Accounting(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    class Limits(ctypes.Structure):
        _fields_ = [("Basic", ctypes.c_byte * 64), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    acc, lim = Accounting(), Limits()
    result = {}
    if k.QueryInformationJobObject(_JOB, 1, ctypes.byref(acc), ctypes.sizeof(acc), None):
        result.update(cpu_s=round((acc.TotalUserTime + acc.TotalKernelTime) / 1e7, 2), processes=acc.TotalProcesses)
    if k.QueryInformationJobObject(_JOB, 9, ctypes.byref(lim), ctypes.sizeof(lim), None):
        result["peak_mb"] = round(lim.PeakJobMemoryUsed / 1048576, 1)
    return result


def downloads_folder() -> Path:
    home = Path.home()
    if IS_WINDOWS:
        from uuid import UUID
        from ctypes import POINTER
        import ctypes
        from ctypes import wintypes

        guid_bytes = UUID("374de290-123f-4565-9164-39c4925e467b").bytes_le
        guid = (ctypes.c_byte * 16).from_buffer_copy(guid_bytes)
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                  wintypes.HANDLE, POINTER(ctypes.c_wchar_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ctypes.windll.ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.ole32.CoTaskMemFree.restype = None
        try:
            hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
            if hr == 0 and out.value:
                return Path(out.value)
        finally:
            if out:
                ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
    elif not IS_MAC:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
        try:
            for line in config.read_text("utf-8").splitlines():
                if line.startswith("XDG_DOWNLOAD_DIR="):
                    path = Path(line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(home)))
                    if path.is_dir():
                        return path
        except OSError:
            pass
    return home / "Downloads"


def _launch(command: list[str]) -> None:
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=not IS_WINDOWS, creationflags=NO_WINDOW)


def reveal(path: Path) -> None:
    if IS_WINDOWS:
        subprocess.Popen(["explorer.exe", "/select,", str(path)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW | BREAKAWAY_FROM_JOB)
    elif IS_MAC:
        _launch(["open", "-R", str(path)])
    else:
        _launch(["xdg-open", str(Path(path).parent)])


def open_path(path: Path) -> None:
    if IS_WINDOWS:
        # ShellExecute from the host would inherit its kill-on-close Job Object.
        subprocess.run([sys.executable, "-I", "-c", "import os, sys; os.startfile(sys.argv[1])", str(path)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=NO_WINDOW | BREAKAWAY_FROM_JOB, check=True)
    else:
        _launch(["open" if IS_MAC else "xdg-open", str(path)])


def clipboard_set_posix(text: str) -> None:
    options = [["pbcopy"]] if IS_MAC else [["wl-copy"], ["xclip", "-selection", "clipboard"],
                                           ["xsel", "--clipboard", "--input"]]
    for command in options:
        exe = shutil.which(command[0])
        if exe:
            subprocess.run([exe, *command[1:]], input=text.encode("utf-8"), check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    raise RuntimeError("Sin herramienta de portapapeles (pbcopy, wl-copy, xclip o xsel).")
