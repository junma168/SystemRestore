"""Windows 系统交互工具：管理员判断、命令执行、可执行文件查找、PATH 刷新。"""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

try:
    import winreg
except ImportError:  # 非 Windows 环境（开发调试用）
    winreg = None

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def resolve_exe(name: str) -> str:
    """把裸命令名解析为完整路径（Windows 上 npm/code 等是 .cmd，CreateProcess 不会自动补全）。"""
    if os.sep in name or (os.altsep and os.altsep in name) \
            or name.lower().endswith((".exe", ".cmd", ".bat", ".com")):
        return name
    return shutil.which(name) or name


def run(cmd: Sequence[str], timeout: int = 120, encoding: Optional[str] = None):
    """执行命令，返回 (returncode, stdout, stderr)。encoding=None 时返回 bytes。"""
    cmd = [str(c) for c in cmd]
    if cmd:
        cmd[0] = resolve_exe(cmd[0])
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            encoding=encoding,
            errors="replace" if encoding else None,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        out = e.stdout
        if out is None:
            out = "" if encoding else b""
        return 124, out, ("超时" if encoding else b"timeout")
    except FileNotFoundError:
        return 127, "" if encoding else b"", (f"找不到命令: {cmd[0]}" if encoding else b"not found")
    except OSError as e:
        return 126, "" if encoding else b"", (str(e) if encoding else str(e).encode())


def run_text(cmd: Sequence[str], timeout: int = 120):
    rc, out, err = run(cmd, timeout=timeout, encoding="utf-8")
    return rc, out or "", err or ""


def expand(path: str) -> str:
    """展开 ~ 和环境变量。"""
    return os.path.expandvars(os.path.expanduser(str(path)))


def find_exe(name: str, extra_dirs=()) -> Optional[str]:
    """查找可执行文件：先 PATH，再常见安装目录。"""
    p = shutil.which(name)
    if p:
        return p
    for d in extra_dirs:
        for ext in ("", ".exe", ".cmd", ".bat"):
            c = Path(expand(d)) / (name + ext)
            if c.is_file():
                return str(c)
    return None


def winget_exe() -> Optional[str]:
    return find_exe("winget", extra_dirs=[r"%LOCALAPPDATA%\Microsoft\WindowsApps"])


def code_exe() -> Optional[str]:
    return find_exe("code", extra_dirs=[
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin",
        r"%ProgramFiles%\Microsoft VS Code\bin",
    ])


def refresh_path_env() -> None:
    """把注册表中最新的系统/用户 PATH 合并进当前进程环境（安装新软件后 PATH 不会自动生效）。"""
    if winreg is None:
        return
    paths: list[str] = []
    for root, sub in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ):
        try:
            k = winreg.OpenKey(root, sub, 0, winreg.KEY_READ)
            v, _ = winreg.QueryValueEx(k, "Path")
            winreg.CloseKey(k)
            paths += [p for p in str(v).split(";") if p.strip()]
        except OSError:
            continue
    if paths:
        os.environ["PATH"] = ";".join(paths) + ";" + os.environ.get("PATH", "")


def relaunch_as_admin(argv=None) -> bool:
    """以管理员权限重启自身（会弹 UAC）。"""
    if is_admin():
        return False
    import sys
    argv = list(argv) if argv is not None else list(sys.argv)
    exe = sys.executable
    if exe.lower().endswith("python.exe") and len(argv) > 1:
        # 源码运行：python run.py ...
        script = str(Path(argv[1]).resolve())
        params = " ".join(f'"{a}"' for a in [script] + argv[2:])
    else:
        params = " ".join(f'"{a}"' for a in argv[1:])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return int(rc) > 32
    except Exception:
        return False


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


# FOLDERID_Desktop（桌面已知文件夹，兼容 OneDrive 重定向）
_FOLDERID_DESKTOP = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                          (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))


def desktop_dir() -> Path:
    """当前用户的桌面目录（优先用 Shell 已知文件夹，兼容 OneDrive 重定向）。"""
    if os.name == "nt":
        try:
            ptr = ctypes.c_wchar_p()
            hr = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(_FOLDERID_DESKTOP), 0, None, ctypes.byref(ptr))
            if hr == 0 and ptr.value:
                p = Path(ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(ptr.value))
                if p.is_dir():
                    return p
        except Exception:
            pass
    for cand in (Path.home() / "Desktop", Path.home() / "桌面"):
        if cand.is_dir():
            return cand
    return Path.home()


def default_work_dir() -> Path:
    """工具默认工作目录（存放清单与配置备份）：桌面下的 SystemRestore 文件夹。"""
    return desktop_dir() / "SystemRestore"


def machine_info() -> dict:
    return {
        "machine": platform.node(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "user": os.environ.get("USERNAME", ""),
        "user_profile": str(Path.home()),
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
    }
