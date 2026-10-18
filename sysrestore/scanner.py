"""系统扫描：收集已安装软件、开发环境、WSL、npm/pip 包与配置文件。

扫描全程只读（唯一例外是「配置文件备份」会把少量配置文件复制到备份目录）。
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import model
from .descriptions import fill_descriptions
from .model import (
    CAT_APP, CAT_CONFIG, CAT_DEV, CAT_LIB, CAT_NPM, CAT_OTHER, CAT_PIP, CAT_STORE, CAT_WSL,
    M_CHOCO, M_CONFIG, M_LOCAL, M_MANUAL, M_NPM, M_PIP, M_SCOOP, M_URL, M_WINGET, M_WINGET_STORE, M_WSL,
    Item, Manifest, norm_core, norm_name,
)
from . import system

try:
    import winreg
except ImportError:
    winreg = None

# ---------------- 分类启发式 ----------------
LIB_HINTS = [
    "redistributable", "runtime", "directx", "visual c++", "vcredist",
    "xna", "silverlight", ".net framework", "webview", "dav1d",
]
DEV_HINTS = [
    "python", "node", "java", "jdk", "jre", "golang", "go ", "rust", "git",
    "visual studio", "vs code", "vscode", "docker", "cmake", "llvm", "clang",
    "mingw", "msys", "anaconda", "jetbrains", "pycharm", "intellij", "goland",
    "clion", "webstorm", "rider", "datagrip", "android", "sdk", "ndk", "dotnet",
    ".net", "maven", "gradle", "postman", "dbeaver", "navicat", "mysql", "redis",
    "mongodb", "terminal", "winscp", "xshell", "xftp", "sublime", "notepad++",
    "vmware", "virtualbox", "wsl", "ffmpeg", "powertoys", "neovim", "vim", "emacs",
    "nvm", "fnm", "rustup", "scoop", "chocolatey",
]

# 配置文件备份目标：key -> (显示名, 规格)
CONFIG_TARGETS = [
    ("vscode", "VS Code 用户配置", {
        "dir": "%APPDATA%/Code/User",
        "files": ["settings.json", "keybindings.json"],
        "dirs": ["snippets"],
    }),
    ("windows-terminal", "Windows Terminal 配置", {
        "files": ["%LOCALAPPDATA%/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json"],
    }),
    ("git", "Git 全局配置", {"files": ["~/.gitconfig"]}),
    ("npm", "npm 配置", {"files": ["~/.npmrc"]}),
    ("pip", "pip 配置", {"files": ["~/pip/pip.ini", "%APPDATA%/pip/pip.ini"]}),
    ("powershell", "PowerShell 配置文件", {
        "files": ["~/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
                  "~/Documents/PowerShell/Microsoft.PowerShell_profile.ps1"],
    }),
    ("ssh-config", "SSH 客户端配置（不含私钥）", {"files": ["~/.ssh/config"]}),
    ("cursor", "Cursor 用户配置", {
        "dir": "%APPDATA%/Cursor/User",
        "files": ["settings.json", "keybindings.json"],
        "dirs": ["snippets"],
    }),
]

# 注册表噪声过滤
_NOISE_PREFIX = ("update for", "hotfix for", "security update for", "service pack for", "kb")
_NOISE_SUBSTR = (
    "net.native", "vclibs", "ui.xaml", "windowsappruntime", "appinstaller",
    "microsoft.services", "advertising",
    "驱动程序包", "driver package", "microsoft update health tools",
)

# Windows 全新安装后自带的软件/环境（重装系统后无需恢复，默认排除）
_INBOX_REGISTRY_SUBSTR = (
    "microsoft edge",      # Edge 浏览器随系统预装
    "microsoft onedrive",  # OneDrive 随 Windows 11 预装
)
_INBOX_WINGET_IDS = {
    "microsoft.edge", "microsoft.edgedev", "microsoft.edgebeta", "microsoft.edgecanary",
    "microsoft.onedrive",
}
# 商店应用包名（小写精确匹配）：微软第一方 inbox 应用
_INBOX_APPX = {
    "microsoft.wallet", "microsoft.mspaint", "microsoft.paint",
    "microsoft.windows.devhome",
    "microsoft.microsoftstickynotes",
    "microsoft.windowscommunicationsapps",              # 邮件和日历
    "microsoft.windowssoundrecorder", "microsoft.screensketch",
    "microsoft.bingsearch", "microsoft.bingweather", "microsoft.bingnews",
    "microsoft.getstarted", "microsoft.gethelp",
    "microsoft.vp9videoextensions", "microsoft.heifimageextension",
    "microsoft.webpimageextension", "microsoft.webmediaextensions",
    "microsoft.yourphone", "microsoft.windowscamera",
    "microsoft.windowsalarms", "microsoft.zunemusic", "microsoft.zunevideo",
    "microsoft.windowscalculator", "microsoft.windowsfeedbackhub",
    "microsoft.storepurchaseapp", "microsoft.microsoftsolitairecollection",
    "microsoft.windows.photos", "microsoft.desktopappinstaller",
    "microsoft.windowsstore", "microsoft.winget.source",
    "microsoft.windowsterminal", "microsoft.windowsnotepad",
    "microsoft.windowsmaps", "microsoft.quickassist", "microsoft.people",
    "microsoft.startexperiencesapp", "microsoft.parentalcontrols",
    "microsoft.net.coreruntime", "microsoft.net.native.runtime",
    "microsoft.bioenrollment", "microsoft.lockapp", "microsoft.asynctextservice",
}
# 商店应用包名前缀（系列匹配）
_INBOX_APPX_PREFIX = (
    "microsoft.languageexperiencepack",           # 语言体验包
    "microsoft.microsoftedge",                    # Edge 各通道
    "microsoft.xbox", "microsoft.gaming",         # Xbox 游戏相关
    "microsoft.windows.client.webexperience",     # 小组件面板
)

# winget 文本列中的版本形态（用于把版本 token 与包 ID 区分开）
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}(?:-[.\w]+)?$")

# 本地安装包文件名里的架构/安装器后缀（美化名称与匹配时剥掉）
_LOCAL_TAIL_RE = re.compile(
    r"[-_ ]?(?:x64|x86|win64|win32|amd64|arm64|64-?bit|32-?bit"
    r"|setup|installer|install|package|安装包|安装程序)$", re.I)
_LOCAL_VER_RE = re.compile(r"[-_ ]?v?(\d+(?:\.\d+){1,3})(?:[-.][\w]+)?$", re.I)


def _pkg_key(s: str) -> str:
    """安装包名/软件名归一化：仅保留小写字母数字与中文，剥掉架构/安装器后缀与版本号。

    必须先剥后缀再剥版本号——否则版本号正则会先把 x64/64-bit 里的数字当成版本吃掉
    （node-v22.11.0-x64 → nodex；Git-2.46.2-64-bit → gitbit）。
    """
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (s or "").lower())
    for _ in range(2):  # 后缀剥露出版本号后再剥一次（如 python312x64）
        s = re.sub(r"(?:x64|x86|win64|win32|amd64|arm64|64bit|32bit"
                   r"|setup|installer|install|package|安装包|安装程序)+$", "", s)
        # 版本号前面必须是字母/汉字，避免吃掉 7-Zip、360软件 等名称里的数字
        s = re.sub(r"(?<=[a-z\u4e00-\u9fff])v?\d+(?:\.\d+)*", "", s)
    return s


def _pretty_name(stem: str) -> str:
    """把安装包文件名美化成软件名：WeChatSetup → WeChat，node-v22.11.0-x64 → node。"""
    s = (stem or "").strip()
    for _ in range(3):  # 后缀可能叠加（-x64、版本号、Setup），最多剥三轮
        s2 = _LOCAL_TAIL_RE.sub("", s)
        s2 = _LOCAL_VER_RE.sub("", s2)
        if s2 == s:
            break
        s = s2
    return s.strip(" -_") or (stem or "本地安装包")


def _extract_version(stem: str) -> str:
    """从安装包文件名提取版本号（找不到返回空串）。"""
    m = re.search(r"v?(\d+(?:\.\d+){1,3})", stem or "")
    return m.group(1) if m else ""


def _is_pkg_id(tok: str) -> bool:
    """判断 token 是否是合法的 winget 包 ID（如 Git.Git / XP9KHM4BK9FZ7Q）。"""
    if not tok or _VERSION_RE.match(tok):
        return False
    if any(c in tok for c in " \\/()"):
        return False
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+#-]*$", tok):
        return False
    if re.match(r"^[\d.+#-]+$", tok):
        return False  # 1.0.2+10 这类纯数字加点号的版本号
    return "." in tok or (tok.isalnum() and any(c.isupper() for c in tok))


@dataclass
class ScanOptions:
    store: bool = True
    wsl: bool = True
    npm: bool = True
    pip: bool = True
    configs: bool = True
    inbox: bool = True   # 排除系统自带组件（Edge/OneDrive/Windows 内置应用等）
    local: bool = True   # 扫描本地安装包目录（.exe/.msi），关联到对应软件条目
    local_dir: Optional[Path] = None  # 本地安装包目录；None 时用 桌面\SystemRestore\packages
    backup_dir: Optional[Path] = None


class Scanner:
    STEPS = [
        "环境检测", "注册表已安装程序", "winget 清单", "Chocolatey/Scoop",
        "商店应用", "开发环境", "VS Code 扩展", "WSL 发行版",
        "npm 全局包", "Python 包", "配置文件备份", "PATH 快照", "本地安装包",
    ]

    def __init__(self, progress: Optional[Callable] = None, log: Optional[Callable] = None):
        self._progress = progress or (lambda *a, **k: None)
        self._log = log or (lambda msg: None)
        self.manifest = Manifest()
        self._tools: dict = {}
        self._skip_inbox = True      # 是否排除系统自带组件
        self._inbox_skipped = 0      # 本次扫描排除掉的系统自带组件数

    # ---------------- 入口 ----------------
    def scan(self, opts: ScanOptions) -> Manifest:
        self.manifest = Manifest()
        self._skip_inbox = opts.inbox
        self._inbox_skipped = 0
        self.manifest.meta = system.machine_info()
        from . import __version__
        self.manifest.meta["tool_version"] = __version__

        step_map = {
            "环境检测": lambda: self._scan_tools(),
            "注册表已安装程序": lambda: self._scan_registry(),
            "winget 清单": lambda: self._scan_winget(),
            "Chocolatey/Scoop": lambda: (self._scan_choco(), self._scan_scoop()),
            "商店应用": lambda: self._scan_store(opts),
            "开发环境": lambda: self._scan_devtools(),
            "VS Code 扩展": lambda: self._scan_vscode_ext(),
            "WSL 发行版": lambda: self._scan_wsl(opts),
            "npm 全局包": lambda: self._scan_npm(opts),
            "Python 包": lambda: self._scan_pip(opts),
            "配置文件备份": lambda: self._scan_configs(opts),
            "PATH 快照": lambda: self._scan_path(),
            "本地安装包": lambda: self._scan_local_installers(opts),
        }
        total = len(self.STEPS)
        for i, step in enumerate(self.STEPS):
            self._progress(step, i, total, f"正在{step}…")
            try:
                step_map[step]()
            except Exception as e:  # 单步失败不影响整体
                self._log(f"[警告] {step} 失败: {e}")
        if self._inbox_skipped:
            self._log(f"已排除 {self._inbox_skipped} 个系统自带组件"
                      "（Edge、OneDrive、Windows 内置应用等，新系统装完即有，无需恢复）")
        self.manifest.meta["inbox_filtered"] = self._inbox_skipped
        self._dedupe()
        # 为每个条目生成用途说明（已填的不覆盖）
        filled = fill_descriptions(self.manifest.items)
        if filled:
            self._log(f"已为 {filled} 个条目生成用途说明")
        self.manifest.meta["tools"] = self._tools
        self._progress("完成", total, total, f"扫描完成，共 {len(self.manifest.items)} 项")
        return self.manifest

    # ---------------- 环境检测 ----------------
    def _scan_tools(self):
        self._tools = {
            "winget": bool(system.winget_exe()),
            "choco": bool(system.find_exe("choco", [r"%ProgramData%\chocolatey\bin", r"%ProgramFiles%\Chocolatey\bin"])),
            "scoop": bool(system.find_exe("scoop", [str(Path.home() / "scoop" / "shims")])),
            "node": bool(system.find_exe("node")),
            "npm": bool(system.find_exe("npm")),
            "python": bool(system.find_exe("python") or system.find_exe("py")),
            "wsl": bool(system.find_exe("wsl")),
            "code": bool(system.code_exe()),
        }

    # ---------------- 注册表 ----------------
    def _scan_registry(self):
        if winreg is None:
            return
        views = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
        ]
        seen = set()
        for root, path, flag in views:
            try:
                key = winreg.OpenKey(root, path, 0, winreg.KEY_READ | flag)
            except OSError:
                continue
            try:
                i = 0
                while True:
                    try:
                        subname = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    info = None
                    try:
                        sub = winreg.OpenKey(key, subname, 0, winreg.KEY_READ | flag)
                        info = self._read_uninstall(sub)
                        winreg.CloseKey(sub)
                    except OSError:
                        continue
                    if not info:
                        continue
                    name = info["name"]
                    if name in seen or self._is_noise(name):
                        continue
                    if self._skip_inbox and self._is_inbox(name):
                        self._inbox_skipped += 1
                        continue
                    seen.add(name)
                    item = Item(
                        name=name,
                        version=info["version"],
                        publisher=info["publisher"],
                        method=M_MANUAL,
                        category=self._categorize(name, info["publisher"]),
                        source="registry",
                        install_location=info["location"],
                        extra={"uninstall": info["uninstall"], "registry_key": subname},
                    )
                    self._enrich_java(item)
                    self.manifest.add(item)
            finally:
                winreg.CloseKey(key)
        self._log(f"注册表已安装程序: {len(seen)} 个")

    @staticmethod
    def _enrich_java(item: Item) -> None:
        """给 JDK 补上 winget 标识（Microsoft Build of OpenJDK 已停更，用 Eclipse Temurin 替代）。"""
        if item.method != M_MANUAL or item.target:
            return
        low = (item.name or "").lower()
        if "openjdk" in low or "jdk" in low or ("java" in low and ("development" in low or "runtime" in low)):
            m = re.search(r"(\d+)", item.name)
            if m:
                major = m.group(1)
                item.method = M_WINGET
                item.target = f"EclipseAdoptium.Temurin.{major}"
                item.notes = f"原机为 {item.name}，改用 Eclipse Temurin {major}（可按需换其他发行版）"

    @staticmethod
    def _read_uninstall(sub) -> Optional[dict]:
        def get(name):
            try:
                v, _ = winreg.QueryValueEx(sub, name)
                return str(v).strip()
            except OSError:
                return ""
        if get("SystemComponent") == "1":
            return None
        name = get("DisplayName")
        if not name:
            return None
        return {
            "name": name,
            "version": get("DisplayVersion"),
            "publisher": get("Publisher"),
            "location": get("InstallLocation"),
            "uninstall": get("QuietUninstallString") or get("UninstallString"),
        }

    @staticmethod
    def _is_noise(name: str) -> bool:
        n = name.lower()
        if any(n.startswith(p) for p in _NOISE_PREFIX):
            return True
        return any(s in n for s in _NOISE_SUBSTR)

    @staticmethod
    def _is_inbox(name: str, pkg_id: str = "") -> bool:
        """判断是否是 Windows 全新安装后自带的软件/环境（重装系统后无需恢复）。

        覆盖三类来源：注册表显示名、winget 包 ID、商店应用包名。
        WebView2 运行时不随系统附带（众多应用依赖它），刻意保留。
        """
        n = (name or "").lower()
        pid = (pkg_id or "").lower()
        if "webview2" in n or "webview2" in pid:
            return False
        if pid and pid in _INBOX_WINGET_IDS:
            return True
        if n in _INBOX_APPX or any(n.startswith(p) for p in _INBOX_APPX_PREFIX):
            return True
        return any(s in n for s in _INBOX_REGISTRY_SUBSTR)

    @staticmethod
    def _categorize(name: str, publisher: str = "") -> str:
        s = f"{name} {publisher}".lower()
        if any(h in s for h in LIB_HINTS):
            return CAT_LIB
        if any(h in s for h in DEV_HINTS):
            return CAT_DEV
        return CAT_APP

    # ---------------- winget ----------------
    def _scan_winget(self):
        exe = system.winget_exe()
        if not exe:
            self._log("未检测到 winget，跳过 winget 清单")
            return
        rc, out, err = system.run_text(
            [exe, "list", "--accept-source-agreements", "--disable-interactivity"], timeout=300)
        if rc != 0 or not out.strip():
            self._log(f"winget list 失败 (rc={rc})")
            return
        rows = self._parse_winget(out)
        added = merged = 0
        for r in rows:
            name, pkg_id, ver, src = r["name"], r["id"], r["version"], r["source"]
            if not name or self._is_noise(name) or self._is_noise(pkg_id):
                continue
            if self._skip_inbox and self._is_inbox(name, pkg_id):
                self._inbox_skipped += 1
                continue
            # 1) 名称列过长时会和 ID 列挤成一个空格，把 ID 从名称里剥出来
            if not pkg_id:
                m = re.match(r"^(.*?)\s+([A-Za-z0-9][\w.+#-]*\.[\w.+#-]+)$", name)
                if m and not re.match(r"^\d+(\.\d+)+", m.group(2)):
                    name, pkg_id = m.group(1).strip(), m.group(2)
            # 2) winget 对未收录软件会用 ARP\... / MSIX\... 冒充 ID，这类无法用于重装
            valid_id = bool(pkg_id) and "\\" not in pkg_id
            method = M_WINGET_STORE if src == "msstore" else M_WINGET
            existing = self.manifest.find_by_name(name)
            if existing is None:
                existing = self.manifest.find_by_core(name, ver)
            if existing is not None:
                if valid_id and existing.method == M_MANUAL and not existing.target:
                    existing.method = method
                    existing.target = pkg_id
                    merged += 1
                    if ver and not existing.version:
                        existing.version = ver
                continue
            if not valid_id:
                continue  # 没有可用的 winget 标识，注册表条目已覆盖该软件
            self.manifest.add(Item(
                name=name, version=ver, method=method, target=pkg_id,
                category=self._categorize(name), source="winget",
            ))
            added += 1
        self._log(f"winget 清单: {len(rows)} 行（可用 {added + merged}，补全标识 {merged}）")

    @staticmethod
    def _parse_winget(out: str) -> list[dict]:
        rows: list[dict] = []
        # 优先尝试 JSON 输出（新版 winget）
        try:
            data = json.loads(out)
            for pkg in data if isinstance(data, list) else []:
                rows.append({
                    "name": str(pkg.get("Name", "")).strip(),
                    "id": str(pkg.get("Id", "")).strip(),
                    "version": str(pkg.get("Version", "")).strip(),
                    "source": str(pkg.get("Source", "winget")).strip().lower(),
                })
            if rows:
                return rows
        except Exception:
            pass
        # 文本输出解析（列以 2+ 空格分隔；名称过长时可能与下一列挤成单空格）
        started = False
        for line in out.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if set(stripped) <= {"-"}:
                started = True
                continue
            if not started:
                continue
            parts = re.split(r"\s{2,}", stripped)
            if len(parts) < 2:
                continue
            name = parts[0].strip()
            rest = parts[1:]
            pkg_id = ""
            for i, tok in enumerate(rest):
                if _is_pkg_id(tok):
                    pkg_id = tok
                    rest = rest[i + 1:]
                    break
            versions = [t for t in rest if _VERSION_RE.match(t)]
            source = "winget"
            for t in rest:
                if t.lower() in ("winget", "msstore"):
                    source = t.lower()
            rows.append({
                "name": name,
                "id": pkg_id,
                "version": versions[0] if versions else "",
                "source": source,
            })
        return rows

    # ---------------- Chocolatey / Scoop ----------------
    def _scan_choco(self):
        exe = system.find_exe("choco", [r"%ProgramData%\chocolatey\bin", r"%ProgramFiles%\Chocolatey\bin"])
        if not exe:
            return
        rc, out, _ = system.run_text(
            [exe, "list", "--local-only", "--limit-output", "--no-color"], timeout=300)
        if rc != 0:
            return
        added = 0
        for line in out.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            name, _, ver = line.partition("|")
            name, ver = name.strip(), ver.strip()
            if not name or name.lower() in ("chocolatey", "chocolatey.core", "chocolatey.extension"):
                continue
            existing = self.manifest.find_by_name(name)
            if existing is not None:
                if existing.method == M_MANUAL and not existing.target:
                    existing.method = M_CHOCO
                    existing.target = name
                    if ver and not existing.version:
                        existing.version = ver
                continue
            self.manifest.add(Item(
                name=name, version=ver, method=M_CHOCO, target=name,
                category=self._categorize(name), source="choco",
            ))
            added += 1
        if added:
            self._log(f"Chocolatey 本地包: 新增 {added}")

    def _scan_scoop(self):
        exe = system.find_exe("scoop", [str(Path.home() / "scoop" / "shims")])
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "list"], timeout=180)
        if rc != 0:
            return
        started = False
        added = 0
        for line in out.splitlines():
            if line.strip().startswith("---"):
                started = True
                continue
            if not started or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            name, ver = parts[0], parts[1]
            existing = self.manifest.find_by_name(name)
            if existing is not None:
                if existing.method == M_MANUAL and not existing.target:
                    existing.method = M_SCOOP
                    existing.target = name
                continue
            self.manifest.add(Item(
                name=name, version=ver, method=M_SCOOP, target=name,
                category=self._categorize(name), source="scoop",
            ))
            added += 1
        if added:
            self._log(f"Scoop 本地包: 新增 {added}")

    # ---------------- 商店应用 ----------------
    def _scan_store(self, opts: ScanOptions):
        if not opts.store:
            return
        rc, out, _ = system.run_text([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-AppxPackage | ForEach-Object { \"$($_.Name)|$($_.Version)|$($_.Publisher)|$($_.SignatureKind)\" }",
        ], timeout=300)
        if rc != 0 or not out.strip():
            return
        skip_pat = re.compile(
            r"^(microsoft\.(net|vclibs|ui|services|winjs|advertising|windowsappruntime)"
            r"|microsoftwindows\.|windows\.)", re.I)
        guid_pat = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-")
        added = 0
        for line in out.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4 or not parts[0]:
                continue
            name, version, signature_kind = parts[0], parts[1], parts[3]
            if skip_pat.match(name) or guid_pat.match(name):
                continue
            if self._skip_inbox and self._is_inbox(name):
                self._inbox_skipped += 1
                continue
            if signature_kind == "System":
                continue  # 系统组件（Hello、锁屏、Xaml 框架等），不是用户安装的应用
            if self.manifest.find_by_name(name):
                continue
            self.manifest.add(Item(
                name=name, version=version, method=M_MANUAL, category=CAT_STORE,
                source="appx", notes="商店应用：请在 Microsoft Store 搜索同名应用重新安装",
            ))
            added += 1
        if added:
            self._log(f"商店应用（未收录于 winget）: {added} 个")

    # ---------------- 开发环境 ----------------
    def _scan_devtools(self):
        self._probe_node()
        self._probe_python()
        self._probe_java()
        self._probe_go()
        self._probe_rust()
        self._probe_dotnet()
        self._probe_git()
        self._probe_vscode()
        self._probe_docker()
        self._probe_cmake()

    def _add_dev(self, name: str, version: str, target: str, notes: str = "", extra: Optional[dict] = None):
        """添加开发环境条目；若注册表已有同名条目且没有安装方式，则补上 winget 标识。"""
        existing = self.manifest.find_by_name(name)
        if existing is not None:
            if existing.method == M_MANUAL and not existing.target and target:
                existing.method = M_WINGET
                existing.target = target
            if version and not existing.version:
                existing.version = version
            if extra:
                existing.extra.update(extra)
            return existing
        return self.manifest.add(Item(
            name=name, version=version, method=M_WINGET, target=target,
            category=CAT_DEV, source="probe", notes=notes, extra=extra or {},
        ))

    def _probe_node(self):
        exe = system.find_exe("node")
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "-v"], timeout=30)
        ver = out.strip().lstrip("v")
        self._add_dev("Node.js", ver, "OpenJS.NodeJS",
                      notes="若原机为 LTS，可改用 OpenJS.NodeJS.LTS", extra={"path": exe})

    def _probe_python(self):
        versions: list[tuple[str, str]] = []
        if system.find_exe("py", [r"%WINDIR%\py.exe"]):
            rc, out, _ = system.run_text(["py", "-0p"], timeout=90)
            for line in out.splitlines():
                m = re.match(r"\s*-V:(\d+\.\d+)(?:-\d+)?\s*\*?\s*(\S.*)?$", line.strip())
                if m:
                    versions.append((m.group(1), (m.group(2) or "").strip()))
        if not versions:
            py = system.find_exe("python")
            if py:
                rc, out, _ = system.run_text([py, "--version"], timeout=30)
                m = re.search(r"Python\s+(\d+\.\d+(?:\.\d+)?)", out)
                if m:
                    versions.append((m.group(1), py))
        for ver, path in versions:
            self._add_dev(f"Python {ver}", ver, f"Python.Python.{ver}",
                          extra={"path": path} if path else {})

    def _probe_java(self):
        majors: set[str] = set()
        ver_detail = ""
        exe = system.find_exe("java")
        if exe:
            rc, out, err = system.run_text([exe, "-version"], timeout=30)
            text = (out or "") + (err or "")
            m = re.search(r'version "([\d._]+)"', text)
            if m:
                ver_detail = m.group(1)
                majors.add(m.group(1).split(".")[0])
        if winreg is not None:
            try:
                k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft", 0, winreg.KEY_READ)
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    if sub.lower() not in ("jdk", "java development kit", "jre", "java runtime environment"):
                        continue
                    try:
                        k2 = winreg.OpenKey(k, sub, 0, winreg.KEY_READ)
                        j = 0
                        while True:
                            try:
                                v = winreg.EnumKey(k2, j)
                            except OSError:
                                break
                            j += 1
                            m = re.match(r"(\d+)", v)
                            if m:
                                majors.add(m.group(1))
                        winreg.CloseKey(k2)
                    except OSError:
                        continue
                winreg.CloseKey(k)
            except OSError:
                pass
        for major in sorted(majors):
            self._add_dev(f"Java (JDK {major})", ver_detail if len(majors) == 1 else major,
                          f"EclipseAdoptium.Temurin.{major}",
                          notes=f"原机 Java 版本 {ver_detail or major}；可按需改为其他发行版",
                          extra={"java_home": str(exe)} if exe else {})

    def _probe_go(self):
        exe = system.find_exe("go")
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "version"], timeout=30)
        m = re.search(r"go(\d+\.\d+(?:\.\d+)?)", out)
        ver = m.group(1) if m else ""
        self._add_dev("Go", ver, "GoLang.Go", extra={"path": exe})

    def _probe_rust(self):
        exe = system.find_exe("rustc")
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "-V"], timeout=30)
        m = re.search(r"rustc\s+(\S+)", out)
        ver = m.group(1) if m else ""
        toolchains = []
        rustup = system.find_exe("rustup")
        if rustup:
            rc, out2, _ = system.run_text([rustup, "toolchain", "list"], timeout=60)
            toolchains = [l.strip() for l in out2.splitlines() if l.strip()]
        self._add_dev("Rust (rustup)", ver, "Rustlang.Rustup",
                      notes="安装 rustup 后可用 rustup toolchain install 恢复工具链",
                      extra={"toolchains": toolchains})

    def _probe_dotnet(self):
        exe = system.find_exe("dotnet")
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "--list-sdks"], timeout=90)
        majors: list[str] = []
        for line in out.splitlines():
            m = re.match(r"\s*(\d+)\.\d+\.\d+", line)
            if m and m.group(1) not in majors:
                majors.append(m.group(1))
        for major in majors:
            self._add_dev(f".NET SDK {major}", major, f"Microsoft.DotNet.SDK.{major}",
                          extra={"path": exe})

    def _probe_git(self):
        exe = system.find_exe("git")
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "--version"], timeout=30)
        m = re.search(r"git version\s+(\S+)", out)
        ver = m.group(1) if m else ""
        self._add_dev("Git", ver, "Git.Git", extra={"path": exe})

    def _probe_vscode(self):
        exe = system.code_exe()
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "--version"], timeout=60)
        ver = out.splitlines()[0].strip() if out.strip() else ""
        self._add_dev("Microsoft Visual Studio Code", ver, "Microsoft.VisualStudioCode",
                      extra={"path": exe})

    def _probe_docker(self):
        exe = system.find_exe("docker")
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "--version"], timeout=60)
        m = re.search(r"Docker version\s+(\S+)", out)
        ver = m.group(1) if m else ""
        self._add_dev("Docker Desktop", ver, "Docker.DockerDesktop",
                      notes="安装后可能需要重启并启用 WSL2", extra={"path": exe})

    def _probe_cmake(self):
        exe = system.find_exe("cmake")
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "--version"], timeout=30)
        m = re.search(r"cmake version\s+(\S+)", out)
        ver = m.group(1) if m else ""
        self._add_dev("CMake", ver, "Kitware.CMake", extra={"path": exe})

    # ---------------- VS Code 扩展 ----------------
    def _scan_vscode_ext(self):
        exe = system.code_exe()
        if not exe:
            return
        rc, out, _ = system.run_text([exe, "--list-extensions"], timeout=300)
        if rc != 0:
            return
        exts = [l.strip() for l in out.splitlines() if l.strip() and "." in l]
        if not exts:
            return
        item = None
        for it in self.manifest.items:
            if it.category == CAT_DEV and "visual studio code" in (it.name or "").lower() \
                    and "insiders" not in (it.name or "").lower():
                item = it
                break
        if item is None:
            item = Item(name="Microsoft Visual Studio Code", method=M_WINGET,
                        target="Microsoft.VisualStudioCode", category=CAT_DEV, source="probe")
            self.manifest.add(item)
        item.extra["extensions"] = exts
        self._log(f"VS Code 扩展: {len(exts)} 个")

    # ---------------- WSL ----------------
    def _scan_wsl(self, opts: ScanOptions):
        if not opts.wsl or not self._tools.get("wsl"):
            return
        rc, raw, _ = system.run(["wsl", "--list", "--verbose"], timeout=90, encoding="utf-16-le")
        text = (raw or "").replace("\ufeff", "").replace("\x00", "")
        added = 0
        for line in text.splitlines():
            m = re.match(r"^\s*(.+?)\s{2,}(\S+)\s+(\d+)\s*$", line)
            if not m:
                continue
            name = re.sub(r"\s*\((?:默认|Default)\)\s*$", "", m.group(1).strip()).strip()
            state, ver = m.group(2), m.group(3)
            if name.upper() == "NAME" or not name:
                continue
            self.manifest.add(Item(
                name=f"WSL: {name}", version=ver, method=M_WSL, target=name,
                category=CAT_WSL, source="wsl",
                notes=f"状态: {state}；安装后可能需要重启", extra={"wsl_version": ver},
            ))
            added += 1
        if added:
            self._log(f"WSL 发行版: {added} 个")

    # ---------------- npm 全局包 ----------------
    def _scan_npm(self, opts: ScanOptions):
        if not opts.npm or not self._tools.get("npm"):
            return
        rc, out, _ = system.run_text(["npm", "ls", "-g", "--depth=0", "--json"], timeout=300)
        try:
            data = json.loads(out)
        except Exception:
            self._log("npm 全局包解析失败")
            return
        deps = (data or {}).get("dependencies") or {}
        added = 0
        for name, info in deps.items():
            ver = (info or {}).get("version", "") or ""
            self.manifest.add(Item(
                name=f"npm: {name}", version=ver,
                method=M_NPM, target=f"{name}@{ver}" if ver else name,
                category=CAT_NPM, source="npm",
            ))
            added += 1
        if added:
            self._log(f"npm 全局包: {added} 个")

    # ---------------- pip 包 ----------------
    def _scan_pip(self, opts: ScanOptions):
        if not opts.pip or not self._tools.get("python"):
            return
        noise = {"pip", "setuptools", "wheel", "pkg-resources", "pkg_resources"}
        rc, out, _ = system.run_text(
            ["python", "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], timeout=600)
        if rc != 0:
            rc, out, _ = system.run_text(
                ["py", "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], timeout=600)
        try:
            data = json.loads(out)
        except Exception:
            self._log("pip 包列表解析失败")
            return
        added = 0
        for pkg in data or []:
            name = (pkg.get("name") or "").strip()
            ver = (pkg.get("version") or "").strip()
            if not name or name.lower() in noise:
                continue
            self.manifest.add(Item(
                name=f"pip: {name}", version=ver,
                method=M_PIP, target=f"{name}=={ver}" if ver else name,
                category=CAT_PIP, source="pip",
            ))
            added += 1
        if added:
            self._log(f"pip 包: {added} 个")

    # ---------------- 配置文件备份 ----------------
    def _scan_configs(self, opts: ScanOptions):
        if not opts.configs:
            return
        backup = Path(opts.backup_dir) if opts.backup_dir else None
        if backup is None:
            self._log("未指定配置备份目录，跳过配置文件备份")
            return
        backup.mkdir(parents=True, exist_ok=True)
        self.manifest.config_backup_dir = str(backup)
        for key, label, spec in CONFIG_TARGETS:
            records: list[dict] = []
            base = spec.get("dir")
            if base:
                d = Path(system.expand(base))
                if d.is_dir():
                    for f in spec.get("files", []):
                        src = d / f
                        if src.is_file():
                            dst = backup / key / f
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dst)
                            records.append({"orig": str(src), "backup": f"{key}/{f}"})
                    for sub in spec.get("dirs", []):
                        src = d / sub
                        if src.is_dir():
                            dst = backup / key / sub
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                            records.append({"orig": str(src), "backup": f"{key}/{sub}"})
            for f in spec.get("files", []) if not base else []:
                src = Path(system.expand(f))
                if src.is_file():
                    dst = backup / key / src.name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    records.append({"orig": str(src), "backup": f"{key}/{src.name}"})
            if records:
                self.manifest.add(Item(
                    name=label, method=M_CONFIG, target=key, category=CAT_CONFIG,
                    source="config", notes=f"{len(records)} 个文件/目录的备份",
                    extra={"files": records},
                ))
        n = sum(1 for it in self.manifest.items if it.category == CAT_CONFIG)
        if n:
            self._log(f"配置文件备份: {n} 组 -> {backup}")

    # ---------------- PATH 快照 ----------------
    def _scan_path(self):
        sys_path, user_path = "", ""
        if winreg is not None:
            try:
                k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                   r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                                   0, winreg.KEY_READ)
                sys_path, _ = winreg.QueryValueEx(k, "Path")
                winreg.CloseKey(k)
            except OSError:
                pass
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ)
                user_path, _ = winreg.QueryValueEx(k, "Path")
                winreg.CloseKey(k)
            except OSError:
                pass
        self.manifest.path_env = {
            "system": [p for p in str(sys_path).split(";") if p.strip()],
            "user": [p for p in str(user_path).split(";") if p.strip()],
        }

    # ---------------- 本地安装包 ----------------
    # 这些文件不是软件安装包，不纳入扫描（卸载程序、本工具自身）
    _LOCAL_SKIP_NAME = ("unins", "卸载", "systemrestore")

    def _scan_local_installers(self, opts: ScanOptions):
        """扫描本地安装包目录：把 .exe/.msi 关联到对应软件条目（优先本地安装），
        匹配不上的生成独立条目（安装方式=本地安装包）。"""
        if not opts.local:
            return
        folder = opts.local_dir or (system.default_work_dir() / "packages")
        folder = Path(folder)
        self.manifest.meta["local_dir"] = str(folder)
        if not folder.is_dir():
            self._log(f"本地安装包目录不存在（{folder}），跳过；"
                      "可把下载好的 .exe/.msi 放进去后重新扫描")
            return
        try:
            files = [p for p in sorted(folder.iterdir(), key=lambda x: x.name.lower())
                     if p.is_file() and p.suffix.lower() in (".exe", ".msi")
                     and not any(s in p.name.lower() for s in self._LOCAL_SKIP_NAME)]
        except OSError as e:
            self._log(f"读取本地安装包目录失败: {e}")
            return
        attached, created = 0, 0
        for p in files:
            it = self._match_local_item(p.stem)
            if it is not None:
                if not it.local_file:
                    it.local_file = p.name
                    attached += 1
                continue
            self.manifest.add(Item(
                name=_pretty_name(p.stem),
                version=_extract_version(p.stem),
                method=M_LOCAL, target="", local_file=p.name,
                category=self._categorize(p.stem), source="local",
                notes=f"本地安装包 {p.name}（安装目录: {folder}）",
            ))
            created += 1
        if attached or created:
            self._log(f"本地安装包: {attached} 个关联到已有条目，{created} 个新增"
                      f"（目录: {folder}）")
        else:
            self._log(f"本地安装包目录为空或无可识别安装包（{folder}）")

    def _match_local_item(self, stem: str):
        """按名称把安装包文件匹配到清单中已有的软件条目；匹配不上返回 None。"""
        key = _pkg_key(stem)
        if len(key) < 3:
            return None
        best, best_rank = None, -1
        for it in self.manifest.items:
            if it.method in (M_CONFIG, M_NPM, M_PIP, M_WSL, M_LOCAL):
                continue
            for cand in (it.name, it.target):
                nk = _pkg_key(cand)
                if len(nk) < 3:
                    continue
                if nk == key and len(key) >= 3:
                    rank = 3
                elif min(len(nk), len(key)) >= 4 and (nk in key or key in nk):
                    rank = 2
                else:
                    continue
                # 同名/子串命中时，优先开发工具与非手动条目，减少误关联
                rank += 1 if it.category == CAT_DEV else 0
                rank += 1 if it.method != M_MANUAL else 0
                if rank > best_rank:
                    best, best_rank = it, rank
        return best

    # ---------------- 去重 ----------------
    def _dedupe(self):
        """合并同一软件的重复条目（如注册表名与探测名略有差异）。"""
        seen: dict[tuple, Item] = {}
        for it in list(self.manifest.items):
            if it.category != CAT_DEV:
                continue
            core = norm_core(it.name)
            mm = ".".join((it.version or "").split(".")[:2])
            k = (core, mm)
            if k in seen:
                keep = seen[k]
                if keep.method == M_MANUAL and it.method != M_MANUAL:
                    keep.method, keep.target = it.method, it.target
                if not keep.version and it.version:
                    keep.version = it.version
                if it.extra.get("extensions") and not keep.extra.get("extensions"):
                    keep.extra["extensions"] = it.extra["extensions"]
                if it.extra.get("path") and not keep.extra.get("path"):
                    keep.extra["path"] = it.extra["path"]
                self.manifest.remove(it)
            else:
                seen[k] = it
        # 没有版本号的开发工具条目，并入同名的有版本条目
        groups: dict[str, list[Item]] = {}
        for it in self.manifest.items:
            if it.category != CAT_DEV:
                continue
            groups.setdefault(norm_core(it.name), []).append(it)
        for items in groups.values():
            versioned = [it for it in items if it.version]
            if not versioned or len(items) < 2:
                continue
            for it in items:
                if it.version:
                    continue
                keep = next((x for x in versioned if x.method != M_MANUAL), versioned[0])
                if keep.method == M_MANUAL and it.method != M_MANUAL:
                    keep.method, keep.target = it.method, it.target
                if it.extra.get("extensions") and not keep.extra.get("extensions"):
                    keep.extra["extensions"] = it.extra["extensions"]
                if it.extra.get("path") and not keep.extra.get("path"):
                    keep.extra["path"] = it.extra["path"]
                self.manifest.remove(it)
        # 安装标识相同的只保留一个（如 OpenJDK 的注册表名与探测名都指向 Temurin.21）
        by_target: dict[str, Item] = {}
        for it in list(self.manifest.items):
            t = it.target
            if not t or it.method in (M_MANUAL, M_CONFIG, M_NPM, M_PIP, M_URL, M_WSL):
                continue
            if t in by_target:
                keep = by_target[t]
                if not keep.version and it.version:
                    keep.version = it.version
                if it.extra.get("extensions") and not keep.extra.get("extensions"):
                    keep.extra["extensions"] = it.extra["extensions"]
                if it.extra.get("path") and not keep.extra.get("path"):
                    keep.extra["path"] = it.extra["path"]
                self.manifest.remove(it)
            else:
                by_target[t] = it
