"""安装引擎：按清单恢复软件与开发环境。

支持 winget / winget·商店 / Chocolatey / Scoop / 直链静默安装 / 本地安装包(.exe/.msi) /
npm / pip / WSL / 配置恢复。
特性：安装前幂等检查、本地安装包优先（找不到自动回退原安装方式）、指定版本不可用时自动回退最新版、
单条失败不影响其余、可取消、安装结束生成报告、可补齐用户 PATH（只增不删）、
安装后自动配置环境变量（PATH / JAVA_HOME）。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import model, system
from .model import (
    CAT_DEV, CAT_LIB,
    M_CHOCO, M_CONFIG, M_LOCAL, M_MANUAL, M_NPM, M_PIP, M_SCOOP, M_URL, M_WINGET, M_WINGET_STORE, M_WSL,
    Item, Manifest,
)

S_PENDING = "等待"
S_RUNNING = "安装中"
S_OK = "成功"
S_FAILED = "失败"
S_SKIPPED = "跳过"
S_MANUAL = "需手动"
S_EXISTS = "已存在"

_STATUS_TAG = {
    S_OK: "ok", S_FAILED: "fail", S_MANUAL: "manual", S_SKIPPED: "skip",
    S_RUNNING: "running", S_EXISTS: "exists",
}


def order_key(it: Item):
    """安装顺序：运行库 -> 开发工具(内部再排 git/node/python 等) -> 应用 -> 商店 -> WSL -> npm -> pip -> 配置。"""
    cat_rank = model.INSTALL_ORDER.get(it.category, 8)
    dev_rank = 99
    if it.category == CAT_DEV:
        t = (it.target or "").lower()
        for i, k in enumerate(model.DEV_INSTALL_PRIORITY):
            if k in t:
                dev_rank = i
                break
    return (cat_rank, dev_rank, (it.name or "").lower())


def resolve_local_installer(name: str, local_dir=None, manifest_path=None) -> Optional[Path]:
    """把条目记录的安装包名解析为真实文件路径；找不到返回 None。

    查找顺序：绝对路径 → 本地安装包目录 → 清单目录(及 packages 子目录) → 清单上级目录 → 桌面默认目录。
    """
    name = (name or "").strip()
    if not name:
        return None
    p = Path(name)
    cands: list[Path] = []
    if p.is_absolute():
        cands.append(p)
    else:
        if local_dir:
            cands.append(Path(local_dir) / name)
        if manifest_path:
            base = Path(manifest_path).parent
            cands += [base / name, base / "packages" / name,
                      base.parent / name, base.parent / "packages" / name]
        cands.append(system.default_work_dir() / "packages" / name)
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


class Installer:
    def __init__(self, manifest: Manifest, manifest_path=None, *, dry_run: bool = False,
                 on_log: Optional[Callable] = None, on_item: Optional[Callable] = None,
                 on_progress: Optional[Callable] = None, on_finish: Optional[Callable] = None,
                 presence: Optional[dict] = None, force: bool = False):
        self.manifest = manifest
        self.manifest_path = Path(manifest_path) if manifest_path else None
        self.dry_run = dry_run
        self._on_log = on_log or (lambda m: None)
        self._on_item = on_item or (lambda *a: None)
        self._on_progress = on_progress or (lambda *a: None)
        self._on_finish = on_finish or (lambda *a: None)
        self.cancel_event = threading.Event()
        self.report_path: Optional[Path] = None
        self._proc: Optional[subprocess.Popen] = None
        # presence: {item.key: {"state": "exists"/"missing", "detail": ...}} 新系统现状检测结果
        self.presence = presence
        self.force = force  # True 时对已存在的条目也强制重装
        # 本地安装包目录（随清单携带；换机器后按常见位置回退查找）
        self.local_dir = manifest.local_dir(self.manifest_path)

    # ---------------- 主流程 ----------------
    def run(self, keys: Optional[set] = None) -> dict:
        items = [it for it in self.manifest.items if it.enabled]
        if keys is not None:
            items = [it for it in items if it.key in keys]
        items.sort(key=order_key)
        total = len(items)
        results: list[tuple[Item, str, str]] = []
        # 安装前比对新系统现状：已存在的自动跳过（除非 force）
        if self.presence is None and not self.force:
            self._log("==== 正在检测新系统现状（已安装的将自动跳过）… ====")
            from .probe import SystemProbe
            probe = SystemProbe(
                progress=lambda step, i, n, m: self._log(f"  [{min(i + 1, n)}/{n}] {m}"),
                log=self._on_log,
            )
            self.presence = probe.check_items(items)
            n_exists = sum(1 for r in self.presence.values()
                           if r.get("state") == "exists")
            self._log(f"==== 现状检测完成：{n_exists} 项已存在（将跳过），"
                      f"{total - n_exists} 项需要安装 ====")
        self._log(f"==== 开始一键安装：共 {total} 项（dry_run={self.dry_run}） ====")
        done = 0
        for it in items:
            if self.cancel_event.is_set():
                self._on_item(it, S_SKIPPED, "用户取消")
                results.append((it, S_SKIPPED, "用户取消"))
                continue
            pres = (self.presence or {}).get(it.key)
            if pres and pres.get("state") == "exists" and not self.force:
                status, msg = self._handle_exists(it, pres)
                self._on_item(it, status, msg)
                results.append((it, status, msg))
                done += 1
                self._on_progress(done, total)
                continue
            self._on_item(it, S_RUNNING, "")
            status, msg = self._install_item(it)
            self._on_item(it, status, msg)
            results.append((it, status, msg))
            done += 1
            self._on_progress(done, total)
        summary = self._summarize(results)
        env_lines = self._autofix_env(results) if not self.dry_run else []
        self._write_report(results, summary, extra=env_lines)
        self._log(f"==== 安装结束：成功 {summary['ok']}，失败 {summary['failed']}，"
                  f"需手动 {summary['manual']}，已存在跳过 {summary['exists']}，"
                  f"跳过 {summary['skipped']} ====")
        self._on_finish(summary, results)
        return summary

    def _autofix_env(self, results) -> list[str]:
        """安装结束后自动配置环境变量：把刚装好的开发工具目录补进用户 PATH、设 JAVA_HOME 等。
        返回写入报告的人类可读行列表。"""
        installed = [it for it, st, _ in results if st in (S_OK, S_EXISTS)]
        if not installed:
            return []
        self._log("==== 自动配置环境变量（PATH / JAVA_HOME 等，仅写入当前用户，只增不删） ====")
        lines: list[str] = []
        try:
            res = ensure_environment(installed, log=self._on_log)
        except Exception as e:  # noqa: BLE001 环境变量配置失败不影响安装结果
            msg = f"环境变量自动配置失败（不影响安装结果）: {e}"
            self._log(msg)
            return [msg]
        if res["added"] or res["env"]:
            if res["added"]:
                lines.append("已添加到用户 PATH：" + "；".join(res["added"]))
            for k, v in res["env"].items():
                lines.append(f"已设置环境变量 {k} = {v}")
            lines.append("环境变量对新打开的终端 / 程序生效；已运行的程序需重启后生效")
        else:
            lines.append("各工具目录均已在 PATH 中，无需改动")
        for l in lines:
            self._log(l)
        return lines

    def _handle_exists(self, it: Item, pres: dict) -> tuple[str, str]:
        """条目已存在：默认跳过；若 VS Code 扩展有缺口则补装缺失扩展。"""
        missing_exts = pres.get("missing_exts") or []
        if missing_exts and it.category == CAT_DEV:
            self._log(f"VS Code 已存在，补装缺失的 {len(missing_exts)} 个扩展…")
            ok = self._install_vscode_exts(it, missing_exts)
            if ok == len(missing_exts):
                return S_OK, f"{pres.get('detail') or '已存在'}；补齐 {ok} 个扩展"
            return S_OK, f"{pres.get('detail') or '已存在'}；补齐 {ok}/{len(missing_exts)} 个扩展"
        return S_EXISTS, pres.get("detail") or "新系统已存在，跳过安装"

    def stop(self):
        self.cancel_event.set()
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass

    # ---------------- 单条安装 ----------------
    def _install_item(self, it: Item) -> tuple[str, str]:
        try:
            if it.method == M_MANUAL and not it.local_file:
                return S_MANUAL, ("需要手动安装：" + it.notes) if it.notes else "需要手动安装（无自动安装方式）"
            # 本地安装包优先：条目带 local_file 且文件找得到就走本地安装；
            # 找不到时 method=local 明确失败，其他方式按原安装方式回退（不阻断恢复）
            local_name = it.local_file or (it.target if it.method == M_LOCAL else "")
            if it.method == M_LOCAL or (local_name and it.method != M_CONFIG):
                path = resolve_local_installer(local_name, self.local_dir, self.manifest_path)
                if path is not None:
                    if it.method != M_LOCAL:
                        self._log(f"「{it.name}」发现本地安装包 {path.name}，优先本地安装")
                    return self._install_local(it, path)
                if it.method == M_LOCAL:
                    where = str(self.local_dir) if self.local_dir else "未设置"
                    return S_FAILED, (f"找不到本地安装包 {local_name!r}（查找目录：{where}；"
                                      f"请把安装包放进该目录，或改用其他安装方式）")
                self._log(f"「{it.name}」未找到本地安装包 {local_name!r}，"
                          f"回退按 {it.method_name()} 安装")
            if it.method in (M_WINGET, M_WINGET_STORE):
                return self._install_winget(it)
            if it.method == M_CHOCO:
                return self._install_choco(it)
            if it.method == M_SCOOP:
                return self._install_scoop(it)
            if it.method == M_NPM:
                return self._install_npm(it)
            if it.method == M_PIP:
                return self._install_pip(it)
            if it.method == M_WSL:
                return self._install_wsl(it)
            if it.method == M_CONFIG:
                return self._restore_config(it)
            if it.method == M_URL:
                return self._install_url(it)
            return S_SKIPPED, f"不支持的安装方式: {it.method}"
        except Exception as e:
            return S_FAILED, f"异常: {e}"

    def _install_local(self, it: Item, path: Path) -> tuple[str, str]:
        """运行本地安装包（.msi 走 msiexec 静默；.exe 用静默参数，缺省 /S）。"""
        path = Path(path)
        self._log(f"使用本地安装包: {path}")
        if self.dry_run:
            return S_OK, f"(dry-run) 将运行本地安装包 {path.name}"
        args = shlex.split(it.silent_args) if it.silent_args else []
        if path.suffix.lower() == ".msi":
            cmd = ["msiexec", "/i", str(path), "/norestart"]
            cmd += args if args else ["/qn"]
            rc, out = self._exec(cmd, timeout=3600)
            if rc in (0, 3010):
                self._maybe_install_vscode_exts(it)
                return S_OK, "本地 MSI 静默安装完成" + ("（需重启）" if rc == 3010 else "")
            return S_FAILED, f"msiexec 退出码 {rc}（{path.name}）"
        cmd = [str(path)] + (args if args else ["/S"])
        rc, out = self._exec(cmd, timeout=3600)
        if rc in (0, 3010):
            self._maybe_install_vscode_exts(it)
            return S_OK, "本地安装包安装完成" + ("（需重启）" if rc == 3010 else "")
        return S_FAILED, f"安装程序退出码 {rc}（{path.name}）；可尝试在条目编辑里补静默参数"

    # ---------------- 各安装方式 ----------------
    def _install_winget(self, it: Item) -> tuple[str, str]:
        exe = system.winget_exe()
        if not exe:
            return S_FAILED, "未找到 winget（可先安装 Microsoft Store 的「应用安装程序」）"
        src = ["--source", "msstore"] if it.method == M_WINGET_STORE else []
        base = [exe, "install", "-e", "--id", it.target, "--silent", "--disable-interactivity",
                "--accept-package-agreements", "--accept-source-agreements"] + src
        rc, out = self._exec(base + (["--version", it.version] if it.version else []), timeout=1800)
        text = self._text(out)
        if self._ok(rc, text):
            self._maybe_install_vscode_exts(it)
            return S_OK, self._brief(text)
        # 指定版本不存在 -> 回退最新版重试一次
        if it.version:
            rc2, out2 = self._exec(base, timeout=1800)
            text2 = self._text(out2)
            if self._ok(rc2, text2):
                self._maybe_install_vscode_exts(it)
                return S_OK, f"指定版本 {it.version} 不可用，已安装最新版"
            return S_FAILED, self._brief(text2) or f"winget 退出码 {rc2}"
        return S_FAILED, self._brief(text) or f"winget 退出码 {rc}"

    def _maybe_install_vscode_exts(self, it: Item):
        exts = it.extra.get("extensions") or []
        if not exts or it.category != CAT_DEV:
            return
        self._install_vscode_exts(it, exts)

    def _install_vscode_exts(self, it: Item, exts: list) -> int:
        """安装指定的 VS Code 扩展列表，返回成功数量。"""
        exts = [e for e in (exts or []) if e]
        if not exts:
            return 0
        code = system.code_exe()
        if not code:
            return 0
        system.refresh_path_env()
        code = system.code_exe() or code
        ok = 0
        for ext in exts:
            if self.cancel_event.is_set():
                break
            rc, out = self._exec([code, "--install-extension", ext, "--force"], timeout=900)
            if rc == 0:
                ok += 1
        self._log(f"VS Code 扩展安装完成: {ok}/{len(exts)}")
        return ok

    def _install_choco(self, it: Item) -> tuple[str, str]:
        exe = system.find_exe("choco", [r"%ProgramData%\chocolatey\bin", r"%ProgramFiles%\Chocolatey\bin"])
        if not exe:
            return S_FAILED, "未找到 chocolatey"
        cmd = [exe, "install", it.target, "-y", "--no-progress", "--ignore-detected-reboot"]
        if it.version:
            cmd.append(f"--version={it.version}")
        rc, out = self._exec(cmd, timeout=3600)
        return (S_OK, self._brief(out)) if self._ok(rc, out) else (S_FAILED, self._brief(out) or f"choco 退出码 {rc}")

    def _install_scoop(self, it: Item) -> tuple[str, str]:
        exe = system.find_exe("scoop", [str(Path.home() / "scoop" / "shims")])
        if not exe:
            return S_FAILED, "未找到 scoop"
        target = it.target if not it.version else f"{it.target}@{it.version}"
        rc, out = self._exec([exe, "install", target], timeout=1800)
        return (S_OK, self._brief(out)) if self._ok(rc, out) else (S_FAILED, self._brief(out) or f"scoop 退出码 {rc}")

    def _install_npm(self, it: Item) -> tuple[str, str]:
        system.refresh_path_env()
        exe = system.find_exe("npm")
        if not exe:
            return S_FAILED, "未找到 npm（请先安装 Node.js）"
        rc, out = self._exec([exe, "install", "-g", it.target], timeout=1200)
        return (S_OK, self._brief(out)) if self._ok(rc, out) else (S_FAILED, self._brief(out) or f"npm 退出码 {rc}")

    def _install_pip(self, it: Item) -> tuple[str, str]:
        system.refresh_path_env()
        py = system.find_exe("python") or system.find_exe("py", [r"%WINDIR%\py.exe"])
        if not py:
            return S_FAILED, "未找到 Python（请先安装 Python）"
        rc, out = self._exec([py, "-m", "pip", "install", it.target], timeout=1800)
        return (S_OK, self._brief(out)) if self._ok(rc, out) else (S_FAILED, self._brief(out) or f"pip 退出码 {rc}")

    def _install_wsl(self, it: Item) -> tuple[str, str]:
        rc, raw, _ = system.run(["wsl", "--list", "--quiet"], timeout=90, encoding="utf-16-le")
        names = [l.strip() for l in (raw or "").replace("\ufeff", "").replace("\x00", "").splitlines() if l.strip()]
        if it.target in names:
            return S_OK, "该发行版已存在"
        rc, out = self._exec(["wsl", "--install", "-d", it.target, "--no-launch"], timeout=3600)
        if rc == 0:
            return S_OK, "已安装（可能需要重启后可用）"
        return S_FAILED, self._brief(out) or f"wsl 退出码 {rc}"

    def _restore_config(self, it: Item) -> tuple[str, str]:
        cfg_dir = self.manifest.config_dir(self.manifest_path)
        if not cfg_dir:
            return S_FAILED, f"找不到配置备份目录: {self.manifest.config_backup_dir}"
        files = it.extra.get("files") or []
        old_profile = (self.manifest.meta or {}).get("user_profile", "")
        new_profile = str(Path.home())
        if self.dry_run:
            return S_OK, f"(dry-run) 将恢复 {len(files)} 个文件/目录"
        restored, missing = 0, []
        for rec in files:
            rel = rec.get("backup") or ""
            src = cfg_dir / rel
            if not src.exists():
                missing.append(rel)
                continue
            orig = rec.get("orig") or ""
            dst = Path(orig)
            if old_profile and new_profile and orig.startswith(old_profile) \
                    and orig[len(old_profile):].startswith(("\\", "/")):
                dst = Path(new_profile + orig[len(old_profile):])
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
                restored += 1
            except OSError as e:
                missing.append(f"{rel} ({e})")
        if restored and not missing:
            return S_OK, f"已恢复 {restored} 个文件/目录"
        if restored:
            return S_OK, f"恢复 {restored} 项，缺失 {len(missing)} 项: {', '.join(missing[:3])}"
        return S_FAILED, "备份文件缺失: " + ", ".join(missing[:5])

    def _install_url(self, it: Item) -> tuple[str, str]:
        url = it.target or it.extra.get("url") or ""
        if not re.match(r"^https?://", url or ""):
            return S_MANUAL, f"未提供有效的下载地址（target={url!r}）"
        if self.dry_run:
            return S_OK, f"(dry-run) 将下载 {url}"
        dl = system.default_work_dir() / "downloads"
        dl.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\-]+", "_", it.name)[:60] or "installer"
        ext = Path(url.split("?")[0]).suffix or ".exe"
        dest = dl / (safe + ext)
        rc, out = self._exec(["curl", "-L", "-o", str(dest), url], timeout=3600)
        if rc != 0 or not dest.exists():
            return S_FAILED, f"下载失败 (rc={rc})"
        args = shlex.split(it.silent_args) if it.silent_args else []
        if not args:
            if dest.suffix.lower() == ".msi":
                rc, out = self._exec(["msiexec", "/i", str(dest), "/qn", "/norestart"], timeout=3600)
                if rc in (0, 3010):
                    return S_OK, "MSI 静默安装完成" + ("（需重启）" if rc == 3010 else "")
                return S_FAILED, f"msiexec 退出码 {rc}"
            return S_MANUAL, f"已下载到 {dest}，未提供静默参数，请手动运行安装"
        rc, out = self._exec([str(dest)] + args, timeout=3600)
        if rc in (0, 3010):
            return S_OK, "安装完成" + ("（需重启）" if rc == 3010 else "")
        return S_FAILED, f"安装程序退出码 {rc}（文件: {dest}）"

    # ---------------- 工具 ----------------
    def _exec(self, cmd: list, timeout: int = 600):
        """执行子进程，输出写入日志；返回 (rc, 合并输出)。"""
        cmd = [str(c) for c in cmd]
        if cmd:
            cmd[0] = system.resolve_exe(cmd[0])
        self._log("$ " + " ".join(cmd), "cmd")
        if self.dry_run:
            return 0, "(dry-run 未实际执行)"
        self._proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=system.CREATE_NO_WINDOW,
            encoding="utf-8", errors="replace",
        )
        try:
            out, _ = self._proc.communicate(timeout=timeout)
            rc = self._proc.returncode
        except subprocess.TimeoutExpired:
            try:
                self._proc.kill()
                out, _ = self._proc.communicate(timeout=30)
            except Exception:
                out = ""
            rc = 124
            out = (out or "") + "\n[超时，已终止]"
        self._proc = None
        out = out or ""
        if out.strip():
            self._log(out.rstrip())
        return rc, out

    @staticmethod
    def _ok(rc: int, text: str) -> bool:
        t = (text or "").lower()
        return rc == 0 or "already installed" in t or "已安装" in t

    @staticmethod
    def _text(out) -> str:
        if isinstance(out, bytes):
            return out.decode("utf-8", "replace")
        return out or ""

    @staticmethod
    def _brief(text: str, limit: int = 200) -> str:
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        tail = " | ".join(lines[-3:]) if lines else ""
        return tail[:limit]

    def _log(self, msg: str, level: str = "info"):
        self._on_log(msg)

    @staticmethod
    def _summarize(results) -> dict:
        s = {"ok": 0, "failed": 0, "manual": 0, "skipped": 0, "exists": 0,
             "total": len(results)}
        key = {S_OK: "ok", S_FAILED: "failed", S_MANUAL: "manual", S_SKIPPED: "skipped",
               S_EXISTS: "exists"}
        for _, st, _ in results:
            s[key.get(st, "skipped")] += 1
        return s

    def _write_report(self, results, summary, extra: Optional[list[str]] = None):
        try:
            base = self.manifest_path.parent if self.manifest_path else system.default_work_dir()
            base.mkdir(parents=True, exist_ok=True)
            p = base / f"install-report-{datetime.now():%Y%m%d-%H%M%S}.txt"
            lines = [
                "系统复原工具 - 安装报告",
                f"时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"机器: {platform_node()}",
                f"结果: 成功 {summary['ok']} / 失败 {summary['failed']} / "
                f"需手动 {summary['manual']} / 已存在跳过 {summary['exists']} / "
                f"跳过 {summary['skipped']}",
                "-" * 60,
            ]
            for it, st, msg in results:
                lines.append(f"[{st}] {it.name} {it.version} ({it.method_name()})"
                             + (f" - {msg}" if msg else ""))
            if extra:
                lines.append("-" * 60)
                lines.append("环境变量自动配置:")
                lines += ["  " + l for l in extra]
            p.write_text("\n".join(lines), encoding="utf-8")
            self.report_path = p
        except Exception as e:
            self._log(f"报告写入失败: {e}")


def platform_node() -> str:
    import platform
    return platform.node()


# ---------------- 环境变量自动配置（用户级，只增不删） ----------------
# 规则字段: (关键字[命中 name/target 任一即生效], 默认确认用可执行文件,
#            候选目录[支持 %VAR% 与 * 通配], (子目录, 确认用可执行文件或None) 列表,
#            附带环境变量{名: 值模板, {dir}=命中的安装目录}, (用户级固定目录, 确认用可执行文件) 列表)
_ENV_RULES: list[tuple[tuple[str, ...], str, tuple[str, ...],
                       tuple[tuple[str, Optional[str]], ...], dict, tuple[tuple[str, str], ...]]] = [
    (("node", "nodejs", "openjs"), "node",
     (r"%ProgramFiles%\nodejs", r"%ProgramFiles(x86)%\nodejs",
      r"%LOCALAPPDATA%\Programs\nodejs", r"%USERPROFILE%\scoop\apps\nodejs\current"),
     (("", "node"),), {}, ((r"%APPDATA%\npm", "npm"),)),
    (("python",), "python",
     (r"%LOCALAPPDATA%\Programs\Python\Python*",
      r"%USERPROFILE%\AppData\Local\Programs\Python\Python*",
      r"%ProgramFiles%\Python*", r"%ProgramFiles(x86)%\Python*"),
     (("", "python"), ("Scripts", None)), {}, ()),
    (("java", "jdk", "temurin", "adoptium", "corretto", "zulu", "openjdk"), "javac",
     (r"%ProgramFiles%\Eclipse Adoptium\jdk*", r"%ProgramFiles%\Java\jdk*",
      r"%ProgramFiles%\Microsoft\jdk-*", r"%ProgramFiles%\Zulu\*",
      r"%ProgramFiles%\Amazon Corretto\*", r"%ProgramFiles(x86)%\Java\jdk*"),
     (("bin", "javac"),), {"JAVA_HOME": "{dir}"}, ()),
    (("go", "golang"), "go",
     (r"%ProgramFiles%\Go", r"%ProgramFiles(x86)%\Go",
      r"%USERPROFILE%\scoop\apps\go\current"),
     (("bin", "go"),), {}, ((r"%USERPROFILE%\go\bin", "go"),)),
    (("git",), "git",
     (r"%ProgramFiles%\Git", r"%ProgramFiles(x86)%\Git",
      r"%LOCALAPPDATA%\Programs\Git", r"%USERPROFILE%\scoop\apps\git\current"),
     (("cmd", "git"), ("bin", None)), {}, ()),
    (("rust", "rustup", "cargo"), "cargo",
     (), (), {}, ((r"%USERPROFILE%\.cargo\bin", "cargo"),)),
    (("visual studio code", "vscode"), "code",
     (r"%LOCALAPPDATA%\Programs\Microsoft VS Code", r"%ProgramFiles%\Microsoft VS Code"),
     (("bin", None),), {}, ()),
    (("dotnet", ".net"), "dotnet",
     (r"%ProgramFiles%\dotnet", r"%ProgramFiles(x86)%\dotnet"),
     (("", "dotnet"),), {}, ()),
    (("anaconda", "miniconda", "conda"), "conda",
     (r"%USERPROFILE%\anaconda3", r"%USERPROFILE%\miniconda3",
      r"%PROGRAMDATA%\anaconda3", r"%PROGRAMDATA%\miniconda3"),
     (("", "conda"), ("Scripts", None), ("condabin", None)), {}, ()),
    (("ruby",), "ruby",
     (r"%ProgramFiles%\Ruby*", r"C:\Ruby*"),
     (("bin", "ruby"),), {}, ()),
    (("php",), "php",
     (r"%ProgramFiles%\php", r"%ProgramFiles(x86)%\php", r"C:\php"),
     (("", "php"),), {}, ()),
    (("mysql", "mariadb"), "mysql",
     (r"%ProgramFiles%\MySQL\MySQL Server*", r"%ProgramFiles(x86)%\MySQL\MySQL Server*",
      r"%ProgramFiles%\MariaDB *"),
     (("bin", "mysql"),), {}, ()),
    (("postgres",), "psql",
     (r"%ProgramFiles%\PostgreSQL\*",),
     (("bin", "psql"),), {}, ()),
    (("maven",), "mvn",
     (r"%ProgramFiles%\apache-maven*", r"%ProgramFiles(x86)%\apache-maven*"),
     (("bin", "mvn"),), {}, ()),
    (("ffmpeg",), "ffmpeg",
     (r"%ProgramFiles%\ffmpeg",),
     (("bin", "ffmpeg"),), {}, ()),
]

# 注册表 Environment 值类型
_REG_SZ = 1
_REG_EXPAND_SZ = 2


def _expand_glob(pattern: str) -> list[Path]:
    """展开 %VAR% 与 * 通配；多版本目录（jdk*、Python3*）按名称倒序，新版本优先。"""
    p = Path(system.expand(pattern))
    if not any(ch in p.name for ch in "*?["):
        return [p]
    if not p.parent.is_dir():
        return []
    try:
        return sorted(p.parent.glob(p.name), key=lambda x: x.name.lower(), reverse=True)
    except OSError:
        return []


def _has_exe(d: Path, exe: Optional[str]) -> bool:
    """目录内是否存在指定可执行文件（exe=None 时只要目录存在即可）。"""
    if not d.is_dir():
        return False
    if not exe:
        return True
    return any((d / (exe + ext)).is_file() for ext in ("", ".exe", ".cmd", ".bat"))


def _rule_dirs(rule) -> tuple[Optional[Path], list[Path], dict]:
    """解析一条规则：返回 (安装目录, 要加入 PATH 的目录列表, 附带环境变量)。未命中返回 (None, [], {})。"""
    _, _, patterns, subdirs, env_tpl, user_dirs = rule
    base: Optional[Path] = None
    dirs: list[Path] = []
    for pat in patterns:  # 候选安装目录：取第一个通配合法且含确认可执行文件的版本
        for b in _expand_glob(pat):
            cand = [(b / sub if sub else b) for sub, _ in subdirs]
            valid = [d for d, (_, exe) in zip(cand, subdirs) if _has_exe(d, exe)]
            if valid:
                base = b
                dirs += valid
                break
        if base is not None:
            break
    extra: list[Path] = []  # 用户级固定目录（npm 全局 bin、cargo bin 等），与候选目录叠加
    for ud, exe in user_dirs:
        d = Path(system.expand(ud))
        if _has_exe(d, exe):
            extra.append(d)
    if base is None and extra:
        base = extra[0]
    env = {k: v.replace("{dir}", str(base)) for k, v in env_tpl.items()} if base is not None else {}
    return base, dirs + extra, env


def _read_user_path() -> tuple[str, int]:
    """读取当前用户 PATH 原值（保留 REG_EXPAND_SZ 类型）。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ)
        v, typ = winreg.QueryValueEx(k, "Path")
        winreg.CloseKey(k)
        return str(v), typ
    except Exception:
        return "", _REG_SZ


def _read_user_env(name: str) -> str:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ)
        v, _ = winreg.QueryValueEx(k, name)
        winreg.CloseKey(k)
        return str(v)
    except Exception:
        return ""


def _write_user_path(entries: list[str], typ: int) -> None:
    import winreg
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(k, "Path", 0, typ, ";".join(entries))
    finally:
        winreg.CloseKey(k)


def _set_user_env(name: str, value: str) -> None:
    import winreg
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(k, name, 0, _REG_SZ, value)
    finally:
        winreg.CloseKey(k)


def _broadcast_env_change() -> None:
    """通知系统环境变量已变化，让资源管理器 / 新开的终端立即生效。"""
    try:
        import ctypes
        HWND_BROADCAST, WM_SETTINGCHANGE = 0xFFFF, 0x1A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 0, 5000, None)
    except Exception:
        pass


def ensure_environment(items, log: Optional[Callable] = None) -> dict:
    """按清单中已安装的开发工具，自动补齐用户 PATH 与环境变量（只增不删，仅写 HKCU）。

    items: 已安装（成功 / 已存在）的条目。返回 {"added": [目录...], "env": {名: 值}, "tools": [条目名...]}。
    """
    log = log or (lambda m: None)
    result: dict = {"added": [], "env": {}, "tools": []}
    if os.name != "nt":
        return result
    matched: dict[str, tuple[Path, list[Path], dict]] = {}
    for it in items or []:
        hay = f"{it.name} {it.target or ''}".lower()
        for rule in _ENV_RULES:
            if not any(k in hay for k in rule[0]):
                continue
            rk = "|".join(rule[0])
            if rk not in matched:
                base, dirs, env = _rule_dirs(rule)
                if base is not None:
                    matched[rk] = (base, dirs, env)
                    result["tools"].append(it.name)
            break  # 每个条目只取第一条命中的规则
    want_path: list[str] = []
    want_env: dict[str, str] = {}
    for _, dirs, env in matched.values():
        want_path += [str(d) for d in dirs]
        want_env.update(env)
    cur, typ = _read_user_path()
    existing = [p.strip() for p in str(cur).split(";") if p.strip()]
    seen = {p.rstrip("\\/").lower() for p in existing}
    added: list[str] = []
    for d in want_path:
        if d.rstrip("\\/").lower() not in seen:
            added.append(d)
            seen.add(d.rstrip("\\/").lower())
    env_done: dict[str, str] = {}
    for name, value in want_env.items():
        old = _read_user_env(name)
        if old and Path(system.expand(old)).is_dir():
            continue  # 已有有效值，不覆盖
        _set_user_env(name, value)
        env_done[name] = value
    if added:
        _write_user_path(existing + added, typ)
    if added or env_done:
        _broadcast_env_change()
    result["added"] = added
    result["env"] = env_done
    if added or env_done:
        log(f"环境变量配置：新增 {len(added)} 个 PATH 条目"
            + (f"，设置 {', '.join(f'{k}={v}' for k, v in env_done.items())}" if env_done else ""))
    return result


# ---------------- 补齐用户 PATH（只增不删） ----------------
def fix_user_path(manifest: Manifest, log: Optional[Callable] = None) -> str:
    """把清单中记录的用户 PATH 快照条目补齐到当前用户环境变量（只补当前存在的路径）。"""
    log = log or (lambda m: None)
    snap = [p.strip() for p in (manifest.path_env or {}).get("user", []) if p.strip()]
    if not snap:
        return "清单中没有用户 PATH 快照"
    cur, typ = _read_user_path()
    existing = [p for p in str(cur).split(";") if p.strip()]
    existing_l = {p.rstrip("\\/").lower() for p in existing}
    add, skipped = [], []
    for p in snap:
        if p.rstrip("\\/").lower() in existing_l:
            continue
        if Path(system.expand(p)).exists():
            add.append(p)
        else:
            skipped.append(p)
    if not add:
        return f"无需补齐（{len(skipped)} 个路径当前不存在，已跳过）"
    _write_user_path(existing + add, typ)
    _broadcast_env_change()
    msg = f"已补齐 {len(add)} 个 PATH 条目（跳过 {len(skipped)} 个不存在的路径）"
    log(msg)
    return msg
