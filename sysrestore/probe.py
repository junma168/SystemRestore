"""一键安装前的「新系统现状检测」。

重装系统后，新系统可能已经自带一部分软件（Windows 内置组件、驱动、运行库），
或者用户已经手动装过一部分，也可能上一次安装中断。本模块在正式安装前把清单
条目与当前系统的实际状态逐一比对：

- 已存在（版本兼容）-> 安装时自动跳过，不再重复安装
- 不存在 / 版本不符  -> 正常安装
- VS Code 已装但扩展缺失 -> 只补装缺失的扩展
- 配置恢复、手动安装项 -> 总是执行（无法自动判定）

用法::

    probe = SystemProbe(log=lambda m: print(m))
    results = probe.check_items(items)   # {item.key: {"state": "exists"/"missing", ...}}
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from . import model, system
from .model import (
    CAT_DEV,
    M_CONFIG, M_MANUAL, M_NPM, M_PIP, M_URL, M_WINGET, M_WINGET_STORE, M_WSL,
)

EXISTS = "exists"
MISSING = "missing"


def _major(ver: str) -> str:
    m = re.match(r"\s*(\d+)", ver or "")
    return m.group(1) if m else ""


def ver_compatible(list_ver: str, actual_ver: str, strict: bool = False) -> bool:
    """清单版本与实际版本是否兼容（用于判断"新系统已经有了"）。

    strict=True（Python/Java/Node/.NET 等开发环境、npm/pip 固定版本包）：
    主、次版本都要一致，否则视为需要安装；
    strict=False（普通应用软件）：主版本一致即视为兼容
    （次版本差异通常不值得重装/降级）。
    任一侧缺版本号时无法比较，按兼容处理。
    """
    a = (list_ver or "").lstrip("vV")
    b = (actual_ver or "").lstrip("vV")
    if not a or not b:
        return True
    pa, pb = a.split("."), b.split(".")
    n = 2 if strict else 1
    for i in range(min(len(pa), len(pb), n)):
        da = re.sub(r"\D", "", pa[i])
        db = re.sub(r"\D", "", pb[i])
        if (da or "0") != (db or "0"):
            return False
    return True


def _split_pkg_target(target: str):
    """从 'name@1.2.3' / 'name==1.2.3' / '@scope/name@1.2.3' 中拆出 (名称, 版本)。"""
    t = (target or "").strip()
    if not t:
        return "", ""
    if "==" in t:
        n, _, v = t.partition("==")
        return n.strip().lower(), v.strip()
    m = re.match(r"^(@[^/]+/)?([^@]+?)(?:@(.+))?$", t)
    if m:
        return (m.group(1) or "") .lower() + m.group(2).lower(), (m.group(3) or "").strip()
    return t.lower(), ""


class SystemProbe:
    """采集当前系统已安装的软件/环境，并与清单条目比对。"""

    STEPS = ("注册表已安装程序", "winget 清单", "商店应用", "开发环境",
             "VS Code 扩展", "WSL 发行版", "npm 全局包", "Python 包")

    def __init__(self, progress: Optional[Callable] = None, log: Optional[Callable] = None):
        self._progress = progress or (lambda *a, **k: None)
        self._log = log or (lambda m: None)
        self.registry: dict[str, list[tuple[str, str]]] = {}   # norm_core -> [(name, ver)]
        self.registry_names: dict[str, str] = {}               # norm_name -> name
        self.winget_ids: dict[str, str] = {}                   # id.lower() -> version
        self.winget_cores: dict[str, str] = {}                 # norm_core(name) -> version
        self.appx_names: set[str] = set()                      # appx Name.lower()
        self.appx_cores: dict[str, str] = {}
        self.npm: dict[str, str] = {}                          # name.lower() -> version
        self.pip: dict[str, str] = {}
        self.exts: set[str] = set()                            # 扩展 id.lower()
        self.tools: dict[str, object] = {}                     # node/go/git/... -> version
        self.python_versions: list[str] = []
        self.java_majors: set[str] = set()
        self.dotnet_majors: list[str] = []
        self.wsl: set[str] = set()                             # 发行版名.lower()

    # ---------------- 采集 ----------------
    _COLLECTORS = ("registry", "winget", "store", "devtools", "exts", "wsl", "npm", "pip")

    def collect(self):
        total = len(self._COLLECTORS)
        for i, key in enumerate(self._COLLECTORS):
            self._progress(key, i, total, f"正在检测{self.STEPS[i]}…")
            try:
                getattr(self, "_c_" + key)()
            except Exception as e:  # 单项采集失败不影响其他
                self._log(f"[警告] 检测{self.STEPS[i]}失败: {e}")
        self._progress("done", total, total, "现状检测完成")

    def _c_registry(self):
        if system.winreg is None:
            return
        from .scanner import Scanner
        views = [
            (system.winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", system.winreg.KEY_WOW64_64KEY),
            (system.winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", system.winreg.KEY_WOW64_32KEY),
            (system.winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
        ]
        n = 0
        for root, path, flag in views:
            try:
                key = system.winreg.OpenKey(root, path, 0, system.winreg.KEY_READ | flag)
            except OSError:
                continue
            try:
                i = 0
                while True:
                    try:
                        subname = system.winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        sub = system.winreg.OpenKey(key, subname, 0,
                                                    system.winreg.KEY_READ | flag)
                        info = Scanner._read_uninstall(sub)
                        system.winreg.CloseKey(sub)
                    except OSError:
                        continue
                    if not info or not info.get("name"):
                        continue
                    name, ver = info["name"], info.get("version") or ""
                    core = model.norm_core(name)
                    self.registry.setdefault(core, []).append((name, ver))
                    self.registry_names.setdefault(model.norm_name(name), name)
                    n += 1
            finally:
                system.winreg.CloseKey(key)
        self._log(f"注册表已安装程序: {n} 条")

    def _c_winget(self):
        exe = system.winget_exe()
        if not exe:
            return
        rc, out, _ = system.run_text(
            [exe, "list", "--accept-source-agreements", "--disable-interactivity"], timeout=300)
        if rc != 0 or not out.strip():
            return
        from .scanner import Scanner
        rows = Scanner._parse_winget(out)
        for r in rows:
            name, pid, ver = r["name"], r["id"], r["version"]
            if pid and "\\" not in pid:
                self.winget_ids[pid.lower()] = ver
            if name:
                self.winget_cores.setdefault(model.norm_core(name), ver)
        self._log(f"winget 清单: {len(rows)} 行")

    def _c_store(self):
        rc, out, _ = system.run_text([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-AppxPackage | ForEach-Object { \"$($_.Name)|$($_.Version)\" }",
        ], timeout=300)
        if rc != 0 or not out.strip():
            return
        for line in out.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2 or not parts[0]:
                continue
            name, ver = parts[0], parts[1]
            self.appx_names.add(name.lower())
            self.appx_cores.setdefault(model.norm_core(name), ver)
        self._log(f"商店应用: {len(self.appx_names)} 个")

    def _c_devtools(self):
        def find(name, extra_dirs=()):
            return system.find_exe(name, extra_dirs)

        exe = find("node")
        if exe:
            _, out, _ = system.run_text([exe, "-v"], timeout=30)
            self.tools["node"] = out.strip().lstrip("v")
        py = find("py", [r"%WINDIR%\py.exe"])
        if py:
            _, out, _ = system.run_text([py, "-0p"], timeout=90)
            for line in out.splitlines():
                m = re.match(r"\s*-V:(\d+\.\d+)", line.strip())
                if m:
                    self.python_versions.append(m.group(1))
        if not self.python_versions:
            py2 = find("python")
            if py2:
                _, out, _ = system.run_text([py2, "--version"], timeout=30)
                m = re.search(r"Python\s+(\d+\.\d+(?:\.\d+)?)", out)
                if m:
                    self.python_versions.append(m.group(1))
        jexe = find("java")
        if jexe:
            _, out, err = system.run_text([jexe, "-version"], timeout=30)
            m = re.search(r'version "([\d._]+)"', (out or "") + (err or ""))
            if m:
                self.tools["java"] = m.group(1)
                self.java_majors.add(_major(m.group(1)))
        if system.winreg is not None:
            try:
                k = system.winreg.OpenKey(system.winreg.HKEY_LOCAL_MACHINE,
                                          r"SOFTWARE\JavaSoft", 0, system.winreg.KEY_READ)
                try:
                    i = 0
                    while True:
                        try:
                            sub = system.winreg.EnumKey(k, i)
                        except OSError:
                            break
                        i += 1
                        if sub.lower() not in ("jdk", "java development kit",
                                               "jre", "java runtime environment"):
                            continue
                        try:
                            k2 = system.winreg.OpenKey(k, sub, 0, system.winreg.KEY_READ)
                            j = 0
                            while True:
                                try:
                                    v = system.winreg.EnumKey(k2, j)
                                except OSError:
                                    break
                                j += 1
                                m = re.match(r"(\d+)", v)
                                if m:
                                    self.java_majors.add(m.group(1))
                            system.winreg.CloseKey(k2)
                        except OSError:
                            continue
                finally:
                    system.winreg.CloseKey(k)
            except OSError:
                pass
        for key, cmd, pat in (("go", ["go", "version"], r"go(\d+\.\d+(?:\.\d+)?)"),
                              ("rust", ["rustc", "-V"], r"rustc\s+(\S+)"),
                              ("git", ["git", "--version"], r"git version\s+(\S+)"),
                              ("docker", ["docker", "--version"], r"Docker version\s+(\S+)"),
                              ("cmake", ["cmake", "--version"], r"cmake version\s+(\S+)")):
            exe = find(cmd[0])
            if exe:
                _, out, _ = system.run_text([exe] + cmd[1:], timeout=60)
                m = re.search(pat, out or "")
                if m:
                    self.tools[key] = m.group(1)
        dex = find("dotnet")
        if dex:
            _, out, _ = system.run_text([dex, "--list-sdks"], timeout=90)
            for line in out.splitlines():
                m = re.match(r"\s*(\d+)\.\d+\.\d+", line)
                if m and m.group(1) not in self.dotnet_majors:
                    self.dotnet_majors.append(m.group(1))
        code = system.code_exe()
        if code:
            _, out, _ = system.run_text([code, "--version"], timeout=60)
            self.tools["code"] = out.splitlines()[0].strip() if out.strip() else ""
        self._log(f"开发环境: {len(self.tools) + len(self.python_versions) + len(self.java_majors)} 项")

    def _c_exts(self):
        code = system.code_exe()
        if not code:
            return
        rc, out, _ = system.run_text([code, "--list-extensions"], timeout=300)
        if rc != 0:
            return
        self.exts = {l.strip().lower() for l in out.splitlines() if l.strip() and "." in l}
        self._log(f"VS Code 扩展: {len(self.exts)} 个")

    def _c_wsl(self):
        rc, raw, _ = system.run(["wsl", "--list", "--quiet"], timeout=90, encoding="utf-16-le")
        text = (raw or "").replace("\ufeff", "").replace("\x00", "")
        for line in text.splitlines():
            name = re.sub(r"\s*\((?:默认|Default)\)\s*$", "", line.strip()).strip()
            if name and name.upper() != "NAME":
                self.wsl.add(name.lower())
        self._log(f"WSL 发行版: {len(self.wsl)} 个")

    def _c_npm(self):
        if not system.find_exe("npm"):
            return
        rc, out, _ = system.run_text(["npm", "ls", "-g", "--depth=0", "--json"], timeout=300)
        try:
            data = json.loads(out)
        except Exception:
            return
        for name, info in ((data or {}).get("dependencies") or {}).items():
            self.npm[name.lower()] = (info or {}).get("version", "") or ""
        self._log(f"npm 全局包: {len(self.npm)} 个")

    def _c_pip(self):
        if not (system.find_exe("python") or system.find_exe("py", [r"%WINDIR%\py.exe"])):
            return
        rc, out, _ = system.run_text(
            ["python", "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            timeout=600)
        if rc != 0:
            rc, out, _ = system.run_text(
                ["py", "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
                timeout=600)
        try:
            data = json.loads(out)
        except Exception:
            return
        for pkg in data or []:
            name = (pkg.get("name") or "").strip()
            if name:
                self.pip[name.lower()] = (pkg.get("version") or "").strip()
        self._log(f"Python 包: {len(self.pip)} 个")

    # ---------------- 比对 ----------------
    def check_items(self, items) -> dict:
        self.collect()
        return {it.key: self.check_item(it) for it in items}

    @staticmethod
    def _exists(detail: str, installed: str = "") -> dict:
        return {"state": EXISTS, "detail": detail, "installed": installed}

    @staticmethod
    def _missing(detail: str = "") -> dict:
        return {"state": MISSING, "detail": detail or "新系统中未检测到"}

    def check_item(self, it) -> dict:
        try:
            return self._check_item(it)
        except Exception as e:  # 单项比对异常按未安装处理（安全方向）
            return self._missing(f"检测异常({e})，按未安装处理")

    def _check_item(self, it) -> dict:
        # 配置恢复：总是执行
        if it.method == M_CONFIG:
            return self._missing("配置恢复（总是执行）")
        # 手动安装项：只能提示，无法自动判定
        if it.method == M_MANUAL:
            return self._missing("需手动安装，无法自动检测")
        # WSL 发行版
        if it.method == M_WSL:
            name = (it.target or "").lower()
            if name and name in self.wsl:
                return self._exists(f"WSL 发行版 {it.target} 已存在")
            return self._missing()
        # npm / pip 全局包（清单固定了版本，版本不符则重装指定版本）
        if it.method == M_NPM:
            name, ver = _split_pkg_target(it.target or it.name)
            actual = self.npm.get(name)
            if actual is not None:
                if ver_compatible(ver, actual, strict=True):
                    return self._exists(f"npm 全局包 {name} 已安装 {actual}", actual)
                return self._missing(f"已安装 {actual}，清单要求 {ver}")
            return self._missing()
        if it.method == M_PIP:
            name, ver = _split_pkg_target(it.target or it.name)
            actual = self.pip.get(name)
            if actual is not None:
                if ver_compatible(ver, actual, strict=True):
                    return self._exists(f"Python 包 {name} 已安装 {actual}", actual)
                return self._missing(f"已安装 {actual}，清单要求 {ver}")
            return self._missing()
        # 商店应用
        if it.method == M_WINGET_STORE:
            nm = model.norm_name(it.name)
            if nm in self.appx_names:
                return self._exists(f"商店应用 {it.name} 已存在")
            return self._missing()
        # 开发环境（按命令行工具判定）
        if it.category == CAT_DEV:
            r = self._check_devtool(it)
            if r is not None:
                return r
        # winget ID 精确匹配（普通应用：同名即视为已有，不按版本降级重装）
        if it.method == M_WINGET:
            tid = (it.target or "").lower()
            if tid and tid in self.winget_ids:
                actual = self.winget_ids[tid]
                return self._exists(f"已安装（{it.target} {actual or '版本未知'}）", actual)
        # 注册表名称模糊匹配（应用软件 / 运行库 / 直链下载项）
        core = model.norm_core(it.name)
        hits = self.registry.get(core)
        if hits:
            best = hits[0]
            detail = f"已安装：{best[0]}"
            if best[1] and best[1] not in best[0]:
                detail += f" {best[1]}"
            return self._exists(detail, best[1])
        if core in self.appx_cores:
            return self._exists(f"已安装（商店应用 {core}）")
        if core in self.winget_cores:
            return self._exists(f"已安装（winget {core} {self.winget_cores[core]}）".strip())
        if it.method == M_URL:
            return self._missing("直链下载项，新系统中未检测到")
        return self._missing()

    def _check_devtool(self, it) -> Optional[dict]:
        """开发环境按命令行探测结果判定；返回 None 表示改用通用匹配。"""
        t = (it.target or "").lower()
        n = (it.name or "").lower()

        def match(*keys):
            return any(k in t or k in n for k in keys)

        if match("nodejs", "node.js"):
            v = self.tools.get("node")
            if v:
                if ver_compatible(it.version, v):  # Node 只比主版本，避免同代 LTS 被降级
                    return self._exists(f"Node.js 已安装 v{v}", v)
                return self._missing(f"已安装 v{v}，清单要求 {it.version}")
            return self._missing()
        if match("python"):
            if self.python_versions:
                for v in self.python_versions:
                    if ver_compatible(it.version, v, strict=True):
                        return self._exists(f"Python {v} 已安装", v)
                return self._missing(f"已安装 {', '.join(self.python_versions)}，清单要求 {it.version}")
            return self._missing()
        if match("temurin", "java", "jdk", "jre"):
            if self.java_majors:
                m = re.search(r"(\d+)", it.name or "")
                want = _major(it.version) or (m.group(1) if m else "")
                if want in self.java_majors:
                    return self._exists(f"Java JDK {want} 已安装", self.tools.get("java", ""))
                return self._missing(f"已安装 JDK {', '.join(sorted(self.java_majors))}，清单要求 {want}")
            return self._missing()
        if match("golang.go", "go"):
            v = self.tools.get("go")
            if v:
                if ver_compatible(it.version, v):  # Go 只比主版本
                    return self._exists(f"Go 已安装 {v}", v)
                return self._missing(f"已安装 {v}，清单要求 {it.version}")
            return self._missing()
        if match("rustup", "rust"):
            v = self.tools.get("rust")
            if v:
                return self._exists(f"Rust 已安装 {v}", v)
            return self._missing()
        if match("dotnet"):
            if self.dotnet_majors:
                want = _major(it.version) or _major(it.name)
                if want in self.dotnet_majors:
                    return self._exists(f".NET SDK {want} 已安装", want)
                return self._missing(f"已安装 SDK {', '.join(self.dotnet_majors)}，清单要求 {want}")
            return self._missing()
        if match("git.git", "git"):
            v = self.tools.get("git")
            if v:
                return self._exists(f"Git 已安装 {v}", v)
            return self._missing()
        if match("visualstudiocode", "visual studio code", "vscode"):
            v = self.tools.get("code")
            if not v:
                return self._missing()
            # VS Code 已装：检查扩展缺口
            exts = it.extra.get("extensions") or []
            if exts and self.exts is not None:
                missing_exts = [e for e in exts if e.lower() not in self.exts]
                if missing_exts:
                    return {"state": EXISTS, "detail": f"VS Code 已安装 {v}，"
                            f"有 {len(missing_exts)}/{len(exts)} 个扩展缺失（将补齐）",
                            "installed": v, "missing_exts": missing_exts}
                return self._exists(f"VS Code 及 {len(exts)} 个扩展均已安装", v)
            return self._exists(f"VS Code 已安装 {v}", v)
        if match("docker"):
            v = self.tools.get("docker")
            if v:
                return self._exists(f"Docker Desktop 已安装 {v}", v)
            return self._missing()
        if match("cmake"):
            v = self.tools.get("cmake")
            if v:
                return self._exists(f"CMake 已安装 {v}", v)
            return self._missing()
        return None
