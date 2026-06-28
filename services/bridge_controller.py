"""启动/停止/查询 SteamVR 手柄链接器（tools/vr_controller_bridge.py）子进程。

用 pidfile 判存活：链接器启动时写 .steamvr/vr_controller_bridge.pid、退出时删除，因此无论它是
本程序拉起的还是 SteamVR 自启动的，都能识别状态并在需要时结束它。状态含过渡态（启动中/关闭中），
供 VR 仪表盘的链接器开关显示。
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _base_dir() -> Path:
    # 运行时可写目录：打包成 exe 时用 exe 所在目录，源码运行时用仓库根
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _pid_path() -> Path:
    return _base_dir() / ".steamvr" / "vr_controller_bridge.pid"


def _log_path() -> Path:
    return _base_dir() / ".steamvr" / "vr_controller_bridge.log"


def _bridge_script() -> Path:
    return _base_dir() / "tools" / "vr_controller_bridge.py"


def _pythonw() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def _launch_cmd() -> list[str]:
    args = ["--quiet", "--log-file", str(_log_path()), "--pid-file", str(_pid_path())]
    if _frozen():
        # 单 exe：用本 exe 以 --run-bridge 再次启动自身来跑链接器
        return [sys.executable, "--run-bridge", *args]
    return [_pythonw(), str(_bridge_script()), *args]


_STILL_ACTIVE = 259
_CREATE_NO_WINDOW = 0x08000000


def _pid_alive(pid: int) -> bool:
    if pid <= 0 or sys.platform != "win32":
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _kill_pid(pid: int) -> None:
    if pid <= 0 or sys.platform != "win32":
        return
    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


class BridgeController:
    START_TIMEOUT = 8.0
    STOP_TIMEOUT = 5.0

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._state = "stopped"   # stopped | starting | running | stopping
        self._deadline = 0.0

    def _read_pid(self) -> int:
        try:
            return int(_pid_path().read_text(encoding="utf-8").strip())
        except Exception:
            return 0

    def _alive(self) -> bool:
        return _pid_alive(self._read_pid())

    def start(self) -> None:
        if self._alive():
            self._state = "running"
            return
        try:
            self._proc = subprocess.Popen(
                _launch_cmd(),
                creationflags=_CREATE_NO_WINDOW,
                cwd=str(_base_dir()),
            )
            self._state = "starting"
            self._deadline = time.monotonic() + self.START_TIMEOUT
        except Exception:
            self._state = "stopped"

    def stop(self) -> None:
        pid = self._read_pid()
        if pid:
            _kill_pid(pid)
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self._state = "stopping"
        self._deadline = time.monotonic() + self.STOP_TIMEOUT

    def toggle(self) -> None:
        if self.status() in ("running", "starting"):
            self.stop()
        else:
            self.start()

    def status(self) -> str:
        """返回 stopped|starting|running|stopping，并把过渡态与实际进程存活情况对齐。"""
        alive = self._alive()
        now = time.monotonic()
        if self._state == "starting":
            if alive:
                self._state = "running"
            elif now > self._deadline:
                self._state = "stopped"
        elif self._state == "stopping":
            if not alive:
                self._state = "stopped"
            elif now > self._deadline:
                _kill_pid(self._read_pid())
                self._state = "stopped"
        elif self._state == "running":
            if not alive:
                self._state = "stopped"
        elif self._state == "stopped":
            if alive:
                self._state = "running"
        return self._state
