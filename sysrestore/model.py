"""数据模型：清单条目(Item)与清单(Manifest)。

清单是一个 JSON 文件，记录重装系统前本机的：
- 已安装程序（注册表 / winget / choco / scoop）
- 开发环境（Node / Python / Java / Go / Rust / .NET / Git / VS Code / Docker 等）
- VS Code 扩展、npm 全局包、pip 包、WSL 发行版、商店应用
- 常用配置文件的备份引用、PATH 快照
"""
from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA = "sysrestore-manifest"
MANIFEST_VERSION = 1

# ---------------- 分类 ----------------
CAT_DEV = "开发工具"
CAT_APP = "应用软件"
CAT_LIB = "运行库"
CAT_STORE = "商店应用"
CAT_WSL = "WSL"
CAT_NPM = "npm 全局包"
CAT_PIP = "Python 包"
CAT_CONFIG = "配置文件"
CAT_OTHER = "其他"
CATEGORIES = [CAT_DEV, CAT_APP, CAT_LIB, CAT_STORE, CAT_WSL, CAT_NPM, CAT_PIP, CAT_CONFIG, CAT_OTHER]

# ---------------- 安装方式 ----------------
M_WINGET = "winget"
M_WINGET_STORE = "winget-store"
M_CHOCO = "choco"
M_SCOOP = "scoop"
M_URL = "url"
M_MANUAL = "manual"
M_NPM = "npm"
M_PIP = "pip"
M_WSL = "wsl"
M_CONFIG = "config"
M_LOCAL = "local"   # 本地安装包（.exe / .msi，随清单文件夹一起拷贝）

METHOD_NAMES = {
    M_WINGET: "winget",
    M_WINGET_STORE: "winget·商店",
    M_CHOCO: "Chocolatey",
    M_SCOOP: "Scoop",
    M_URL: "直链静默安装",
    M_MANUAL: "手动安装",
    M_NPM: "npm -g",
    M_PIP: "pip",
    M_WSL: "WSL",
    M_CONFIG: "配置恢复",
    M_LOCAL: "本地安装包",
}

# 安装顺序：先运行库和开发工具，再应用，最后语言包与配置
INSTALL_ORDER = {
    CAT_LIB: 0, CAT_DEV: 1, CAT_APP: 2, CAT_STORE: 3,
    CAT_WSL: 4, CAT_NPM: 5, CAT_PIP: 6, CAT_CONFIG: 7, CAT_OTHER: 8,
}
# 开发工具内部的安装优先级（git/node/python 先行）
DEV_INSTALL_PRIORITY = ["git", "node", "python", "java", "go", "rust", "dotnet", "visualstudiocode", "docker"]


def norm_name(s: str) -> str:
    """名称归一化：小写、去掉架构后缀与商标符号、压缩空白。"""
    s = (s or "").lower()
    s = re.sub(r"\((?:x64|x86|64-bit|32-bit|win64|amd64|arm64)\)", "", s)
    s = re.sub(r"[™®©]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_core(s: str) -> str:
    """更强的归一化：再去掉版本号、(User) 后缀、构建号与 microsoft 前缀，用于同一软件的模糊匹配。"""
    s = norm_name(s)
    s = re.sub(r"\+\d+", "", s)
    s = re.sub(r"\b\d+(?:\.\d+)+\b", "", s)
    s = re.sub(r"\((?:user|users|all users|默认|default)\)", "", s)
    s = s.replace("microsoft ", "")
    return re.sub(r"\s+", " ", s).strip(" -")


@dataclass
class Item:
    """清单中的一个可恢复条目。"""

    name: str
    version: str = ""
    publisher: str = ""
    method: str = M_MANUAL            # 安装方式
    target: str = ""                  # winget id / choco 包名 / scoop 包名 / 下载地址 / name@ver / WSL 发行名 / 配置键
    silent_args: str = ""             # 直链安装时的静默参数
    category: str = CAT_OTHER
    enabled: bool = True              # 是否参与一键安装
    notes: str = ""                   # 备注（手动安装说明等）
    desc: str = ""                    # 用途说明：这个软件/环境是做什么的（自动生成，可手改）
    local_file: str = ""              # 本地安装包：文件名（相对本地安装包目录）或绝对路径；命中则优先本地安装
    source: str = ""                  # 扫描来源：registry/winget/choco/scoop/appx/probe/npm/pip/wsl/manual/local
    install_location: str = ""
    extra: dict = field(default_factory=dict)   # 扩展信息（如 vscode 扩展列表、配置备份明细）
    scan_time: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in fields})

    @property
    def key(self) -> str:
        return f"{self.method}|{self.target or self.name}"

    def method_name(self) -> str:
        return METHOD_NAMES.get(self.method, self.method)


class Manifest:
    """整机清单。"""

    def __init__(self):
        self.items: list[Item] = []
        self.meta: dict = {}
        self.path_env: dict = {}          # {"system": [...], "user": [...]}
        self.config_backup_dir: str = ""  # 配置文件备份目录（可相对清单文件）

    # ---------------- 基本操作 ----------------
    def add(self, item: Item) -> Item:
        self.items.append(item)
        return item

    def remove(self, item: Item) -> None:
        try:
            self.items.remove(item)
        except ValueError:
            pass

    def find_by_name(self, name: str) -> Optional[Item]:
        n = norm_name(name)
        for it in self.items:
            if norm_name(it.name) == n:
                return it
        return None

    def find_by_core(self, name: str, version: str = "") -> Optional[Item]:
        """按忽略版本号的名称匹配；主版本号(前两段)不一致时视为不同软件（如 Python 3.11 / 3.12）。"""
        core = norm_core(name)
        if not core:
            return None
        mm = ".".join((version or "").split(".")[:2])
        cands = [it for it in self.items if norm_core(it.name) == core]
        if not cands:
            return None
        if len(cands) == 1:
            it = cands[0]
            it_mm = ".".join((it.version or "").split(".")[:2])
            return it if (not mm or not it_mm or mm == it_mm) else None
        for it in cands:
            it_mm = ".".join((it.version or "").split(".")[:2])
            if mm and it_mm and mm == it_mm:
                return it
        return None

    def stats(self) -> dict:
        by_cat: dict[str, int] = {}
        by_method: dict[str, int] = {}
        for it in self.items:
            by_cat[it.category] = by_cat.get(it.category, 0) + 1
            m = it.method_name()
            by_method[m] = by_method.get(m, 0) + 1
        return {
            "total": len(self.items),
            "enabled": sum(1 for it in self.items if it.enabled),
            "by_category": by_cat,
            "by_method": by_method,
        }

    # ---------------- 序列化 ----------------
    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "version": MANIFEST_VERSION,
            "meta": self.meta,
            "path_env": self.path_env,
            "config_backup_dir": self.config_backup_dir,
            "items": [it.to_dict() for it in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        d = d or {}
        if d.get("schema") not in (None, SCHEMA):
            raise ValueError(f"不是有效的系统复原清单文件（schema={d.get('schema')!r}）")
        m = cls()
        m.meta = d.get("meta") or {}
        m.path_env = d.get("path_env") or {}
        m.config_backup_dir = d.get("config_backup_dir") or ""
        for it in d.get("items") or []:
            m.items.append(Item.from_dict(it))
        return m

    @classmethod
    def load(cls, path) -> "Manifest":
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        m = cls.from_dict(data)
        m.meta["manifest_path"] = str(p)
        return m

    def save(self, path, config_dir_relative_to: Optional[Path] = None) -> None:
        """保存清单。若配置备份目录位于清单所在目录下，则记录为相对路径，方便整文件夹拷贝。"""
        p = Path(path)
        if config_dir_relative_to is not None and self.config_backup_dir:
            try:
                rel = Path(self.config_backup_dir).resolve().relative_to(
                    Path(config_dir_relative_to).resolve())
                self.config_backup_dir = str(rel)
            except (ValueError, OSError):
                pass  # 不在同一目录树下，保留绝对路径
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def config_dir(self, manifest_path: Optional[Path] = None) -> Optional[Path]:
        """解析配置备份目录：相对路径基于清单文件所在目录。"""
        if not self.config_backup_dir:
            return None
        d = Path(self.config_backup_dir)
        if not d.is_absolute():
            base = Path(manifest_path).parent if manifest_path else Path.cwd()
            d = base / d
        d = d.resolve()
        return d if d.exists() else None

    def local_dir(self, manifest_path: Optional[Path] = None) -> Optional[Path]:
        """解析本地安装包目录：优先 meta 记录（扫描时用户指定），再按常见位置回退。

        回退顺序保证「清单文件夹整个拷贝到 U 盘/网盘后换机器使用」时也能找到安装包：
        清单目录、清单目录\\packages、清单上级目录、清单上级目录\\packages、桌面\\SystemRestore\\packages。
        """
        cands: list[Path] = []
        recorded = (self.meta or {}).get("local_dir") or ""
        if recorded:
            d = Path(recorded)
            if not d.is_absolute() and manifest_path:
                d = Path(manifest_path).parent / d
            cands.append(d)
        if manifest_path:
            base = Path(manifest_path).parent
            cands += [base, base / "packages", base.parent, base.parent / "packages"]
        cands.append(Path.home() / "Desktop" / "SystemRestore" / "packages")
        cands.append(Path.home() / "桌面" / "SystemRestore" / "packages")
        for c in cands:
            if c.is_dir():
                return c
        return cands[0] if cands else None
