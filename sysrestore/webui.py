"""本地 Web 界面：标准库 http.server + JSON API + 单页前端。

启动后在浏览器打开 http://127.0.0.1:<port>，无需任何第三方依赖。
所有扫描/安装操作在服务端线程中执行，前端轮询进度。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __app_name__, __version__, model, system
from .descriptions import describe, fill_descriptions
from .installer import (Installer, ensure_environment, fix_user_path,
                        order_key, resolve_local_installer,
                        S_PENDING, S_RUNNING, S_OK, S_FAILED, S_SKIPPED, S_MANUAL, S_EXISTS)
from .model import M_LOCAL, M_MANUAL, Item, Manifest
from .probe import EXISTS as P_EXISTS, MISSING as P_MISSING, SystemProbe
from .scanner import ScanOptions, Scanner

_MAX_LOG = 600


def resource_path(name: str) -> Path:
    """兼容源码运行与 PyInstaller 打包后的资源路径。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = Path(base) / "sysrestore" / "web" / name
        if p.exists():
            return p
    return Path(__file__).resolve().parent / "web" / name


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


class AppState:
    """全局应用状态（单用户本地工具，加锁保证线程安全）。"""

    def __init__(self):
        self.lock = threading.RLock()
        self.manifest = Manifest()
        self.manifest_path: Path | None = None
        self.scan = {"running": False, "done": 0, "total": 0, "step": "", "message": "",
                     "error": "", "result": None, "folder": ""}
        self.install = {"running": False, "done": 0, "total": 0, "summary": None,
                        "report": "", "log": deque(maxlen=_MAX_LOG), "items": {}}
        self.probe = {"running": False, "done": 0, "total": 0, "message": "",
                      "error": "", "results": {}, "at": "", "manifest": ""}
        self.installer: Installer | None = None
        self._scan_thread: threading.Thread | None = None
        self._install_thread: threading.Thread | None = None
        self._probe_thread: threading.Thread | None = None
        self._failed: list = []

    # ---------- 清单 ----------
    def load_manifest(self, path) -> str:
        p = Path(path)
        m = Manifest.load(p)
        fill_descriptions(m.items)  # 老清单（无 desc 字段）打开时自动补齐
        with self.lock:
            self.manifest = m
            self.manifest_path = p
        return f"已打开清单 {p}（{len(m.items)} 项）"

    def save_manifest(self, path) -> str:
        p = Path(path)
        if p.is_dir():
            p = p / "manifest.json"  # 用户只选了文件夹：保存为该文件夹下的 manifest.json
        p.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.manifest.save(p, config_dir_relative_to=p.parent)
            self.manifest_path = p
        return f"已保存 {p}"

    def items_payload(self, search: str = "", cat: str = "全部", install_order: bool = False) -> list[dict]:
        search = (search or "").strip().lower()
        out = []
        with self.lock:
            indexed = list(enumerate(self.manifest.items))  # (清单真实索引, 条目)
            if install_order:
                indexed.sort(key=lambda pair: order_key(pair[1]))
            for real_idx, it in indexed:
                if cat and cat != "全部" and it.category != cat:
                    continue
                if search and search not in (
                        f"{it.name} {it.target} {it.publisher} {it.notes} {it.desc} {it.local_file}"
                ).lower():
                    continue
                out.append({
                    "index": real_idx, "name": it.name, "version": it.version,
                    "publisher": it.publisher, "category": it.category,
                    "method": it.method, "method_name": it.method_name(),
                    "target": it.target, "silent_args": it.silent_args,
                    "notes": it.notes, "desc": it.desc, "enabled": it.enabled,
                    "local_file": it.local_file,
                    "install_location": it.install_location, "extra": it.extra,
                })
        return out

    def stats_payload(self) -> dict:
        with self.lock:
            st = self.manifest.stats()
            items = list(self.manifest.items)
        st["admin"] = system.is_admin()
        st["winget"] = bool(system.winget_exe())
        st["manifest_path"] = str(self.manifest_path) if self.manifest_path else ""
        st["config_dir"] = ""
        if self.manifest.config_backup_dir and self.manifest_path:
            cd = self.manifest.config_dir(self.manifest_path)
            st["config_dir"] = str(cd) if cd else ""
        st["manual_count"] = sum(1 for it in items if it.enabled and it.method == M_MANUAL)
        st["winget_missing"] = (not st["winget"]) and any(
            it.enabled and it.method.startswith("winget") for it in items)
        st["default_dir"] = str(system.default_work_dir())
        st["local_count"] = sum(1 for it in items if it.local_file)
        st["local_dir"] = str(self.manifest.local_dir(self.manifest_path) or "")
        return st

    # ---------- 扫描 ----------
    def start_scan(self, opts: dict) -> str:
        with self.lock:
            if self.scan["running"]:
                return "扫描已在进行中"
            folder = Path(opts.get("folder") or str(system.default_work_dir()))
            try:
                folder.mkdir(parents=True, exist_ok=True)  # 桌面下没有 SystemRestore 文件夹时自动创建
            except OSError:
                pass  # 真正保存时再报错
            local_dir_raw = (opts.get("local_dir") or "").strip()
            local_dir = Path(local_dir_raw) if local_dir_raw else (folder / "packages")
            base = folder / datetime.now().strftime("scan-%Y%m%d-%H%M%S")
            self.scan.update({"running": True, "done": 0, "total": 0, "step": "",
                              "message": "准备扫描…", "error": "", "result": None,
                              "folder": str(base)})
            scan_opts = ScanOptions(
                store=bool(opts.get("store", True)),
                wsl=bool(opts.get("wsl", True)),
                npm=bool(opts.get("npm", True)),
                pip=bool(opts.get("pip", True)),
                configs=bool(opts.get("configs", True)),
                inbox=bool(opts.get("inbox", True)),
                local=bool(opts.get("local", True)),
                local_dir=local_dir,
                backup_dir=base / "configs",
            )

        def work():
            try:
                sc = Scanner(
                    progress=lambda s, i, n, m: self._scan_progress(s, i, n, m),
                    log=lambda m: None,
                )
                m = sc.scan(scan_opts)
                m.save(base / "manifest.json", config_dir_relative_to=base)
                inbox_filtered = int(m.meta.get("inbox_filtered", 0) or 0)
                with self.lock:
                    self.manifest = m
                    self.manifest_path = base / "manifest.json"
                    self.scan.update({"running": False, "done": self.scan["total"],
                                      "step": "完成", "message": f"扫描完成，共 {len(m.items)} 项",
                                      "result": {"total": len(m.items),
                                                 "by_category": m.stats()["by_category"],
                                                 "inbox_filtered": inbox_filtered,
                                                 "folder": str(base)}})
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self.scan.update({"running": False, "error": str(e), "message": "扫描失败"})

        self._scan_thread = threading.Thread(target=work, daemon=True)
        self._scan_thread.start()
        return "扫描已开始"

    def _scan_progress(self, step, done, total, message):
        with self.lock:
            self.scan.update({"done": done, "total": total, "step": step, "message": message})

    # ---------- 安装 ----------
    def start_install(self, keys=None, force: bool = False) -> str:
        with self.lock:
            if self.install["running"]:
                return "安装已在进行中"
            if self.probe["running"]:
                return "系统现状检测正在进行，请稍候"
            items = [it for it in self.manifest.items if it.enabled]
            if keys:
                keys = set(keys)
                items = [it for it in items if it.key in keys]
            if not items:
                return "没有勾选任何条目"
            self.install.update({"running": True, "done": 0, "total": len(items),
                                 "summary": None, "report": "", "items": {}})
            self.install["log"].clear()
            self._failed = []

        def work():
            installer = None
            try:
                # 安装前比对新系统现状（除非用户选择强制重装）
                presence = None
                if not force:
                    presence = self._ensure_probe(items)
                installer = Installer(
                    self.manifest, self.manifest_path,
                    on_log=lambda m: self._install_log(m),
                    on_item=lambda it, st, d: self._install_item(it.key, st, d),
                    on_progress=lambda d, t: self._install_progress(d, t),
                    on_finish=lambda s, r: self._install_finish(s, r),
                    presence=presence, force=force,
                )
                with self.lock:
                    self.installer = installer
                installer.run(keys=set(keys) if keys else None)
            except Exception as e:  # noqa: BLE001
                self._install_log(f"安装过程异常: {e}")
                with self.lock:
                    self.install["running"] = False

        self._install_thread = threading.Thread(target=work, daemon=True)
        self._install_thread.start()
        return f"开始安装 {len(items)} 项"

    def _ensure_probe(self, items) -> dict:
        """安装前确保有新鲜的现状检测结果；没有则现场检测（在调用线程内执行）。"""
        with self.lock:
            results = dict(self.probe.get("results") or {})
            fresh = bool(results) and self.probe.get("manifest") == str(self.manifest_path or "")
        if fresh:
            # 已有结果：只补测新增/改动的条目
            todo = [it for it in items if it.key not in results]
            if not todo:
                return results
            self._install_log(f"检测新增的 {len(todo)} 个条目…")
        else:
            todo = list(items)
            self._install_log("==== 正在检测新系统现状（已安装的将自动跳过）… ====")
        probe = SystemProbe(
            progress=lambda step, i, n, m: self._install_log(f"  [{min(i + 1, n)}/{n}] {m}"),
            log=lambda m: self._install_log(m),
        )
        new = probe.check_items(todo)
        with self.lock:
            self.probe["results"].update(new)
            self.probe["at"] = datetime.now().strftime("%H:%M:%S")
            self.probe["manifest"] = str(self.manifest_path or "")
        return dict(self.probe["results"])

    def start_probe(self) -> str:
        """手动触发一次新系统现状检测（只读，不改动任何软件）。"""
        with self.lock:
            if self.probe["running"]:
                return "检测已在进行中"
            if self.install["running"]:
                return "安装正在进行，无法同时检测"
            items = [it for it in self.manifest.items if it.enabled]
            if not items:
                return "清单中没有勾选的条目"
            self.probe.update({"running": True, "done": 0, "total": len(items),
                               "message": "准备检测…", "error": "", "results": {}})

        def work():
            try:
                probe = SystemProbe(
                    progress=lambda step, i, n, m: self._probe_progress(i, n, m),
                    log=lambda m: None,
                )
                results = probe.check_items(items)
                with self.lock:
                    self.probe.update({"running": False, "done": self.probe["total"],
                                       "results": results,
                                       "at": datetime.now().strftime("%H:%M:%S"),
                                       "manifest": str(self.manifest_path or ""),
                                       "message": self._probe_summary(results)})
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self.probe.update({"running": False, "error": str(e), "message": "检测失败"})

        self._probe_thread = threading.Thread(target=work, daemon=True)
        self._probe_thread.start()
        return "开始检测新系统现状"

    @staticmethod
    def _probe_summary(results: dict) -> str:
        n_exists = sum(1 for r in results.values() if r.get("state") == P_EXISTS)
        return f"检测完成：{n_exists} 项已存在（将跳过），{len(results) - n_exists} 项需要安装"

    def _probe_progress(self, done, total, message):
        with self.lock:
            self.probe.update({"done": done, "total": total, "message": message})

    def probe_status_payload(self) -> dict:
        with self.lock:
            results = dict(self.probe.get("results") or {})
            return {
                "running": self.probe["running"],
                "done": self.probe["done"],
                "total": self.probe["total"],
                "message": self.probe["message"],
                "error": self.probe["error"],
                "at": self.probe["at"],
                "summary": {
                    "exists": sum(1 for r in results.values() if r.get("state") == P_EXISTS),
                    "missing": sum(1 for r in results.values() if r.get("state") == P_MISSING),
                },
                "results": {k: {"state": v.get("state"), "detail": v.get("detail", "")}
                            for k, v in results.items()},
            }

    def _install_log(self, msg):
        with self.lock:
            self.install["log"].append(f"[{_now()}] {msg}")

    def _install_item(self, key, status, detail):
        with self.lock:
            self.install["items"][key] = [status, detail]
            if status == S_FAILED:
                self._failed.append(key)

    def _install_progress(self, done, total):
        with self.lock:
            self.install.update({"done": done, "total": total})

    def _install_finish(self, summary, results):
        with self.lock:
            self.install.update({"running": False, "summary": summary,
                                 "report": str(self.installer.report_path or "") if self.installer else ""})

    def stop_install(self):
        with self.lock:
            if self.installer and self.install["running"]:
                self.installer.stop()
                return "正在停止…"
            return "当前没有安装任务"

    # ---------- 退出 ----------
    def request_shutdown(self) -> str:
        """请求退出工具；扫描/安装/现状检测进行中时拒绝退出。"""
        with self.lock:
            busy = [name for name, running in (
                ("扫描", self.scan.get("running")),
                ("安装", self.install.get("running")),
                ("现状检测", self.probe.get("running")),
            ) if running]
        if busy:
            return "ERROR: 正在进行" + "、".join(busy) + "，请先停止或等待完成"
        return "OK"

    def retry_failed(self):
        with self.lock:
            keys = list(self._failed)
        if not keys:
            return "没有失败项"
        return self.start_install(keys=keys)

    def install_status_payload(self) -> dict:
        with self.lock:
            probe_results = {k: {"state": v.get("state"), "detail": v.get("detail", "")}
                             for k, v in (self.probe.get("results") or {}).items()}
            return {
                "running": self.install["running"],
                "done": self.install["done"],
                "total": self.install["total"],
                "summary": self.install["summary"],
                "report": self.install["report"],
                "log": list(self.install["log"])[-200:],
                "items": dict(self.install["items"]),
                "probe": probe_results,
                "local": self._local_files_payload(),
            }

    def _local_files_payload(self) -> dict:
        """各条目本地安装包的就绪状态（文件是否存在、解析到的路径）。"""
        out: dict = {}
        with self.lock:
            items = list(self.manifest.items)
            mpath = self.manifest_path
            ld = self.manifest.local_dir(mpath)
        for it in items:
            name = it.local_file or (it.target if it.method == M_LOCAL else "")
            if not name:
                continue
            p = resolve_local_installer(name, ld, mpath)
            out[it.key] = {"file": name, "found": p is not None,
                           "path": str(p) if p else "", "dir": str(ld) if ld else ""}
        return out

    def scan_status_payload(self) -> dict:
        with self.lock:
            return dict(self.scan)


def _browse(path: str, exts=(".json",)) -> dict:
    p = Path(path or str(Path.home()))
    if not p.is_absolute():
        p = Path.home()
    try:
        p = p.resolve()
    except OSError:
        p = Path.home()
    if not p.exists():
        p = Path.home()
    if p.is_file():
        p = p.parent
    exts = tuple("." + e.lower().lstrip(".") for e in exts if e)
    dirs, files = [], []
    try:
        for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.is_dir():
                dirs.append(entry.name)
            elif not exts or entry.suffix.lower() in exts:
                files.append(entry.name)
    except OSError:
        pass
    drives = []
    import string
    for letter in string.ascii_uppercase:
        d = Path(f"{letter}:\\")
        if d.exists():
            drives.append(str(d))
    return {"path": str(p), "parent": str(p.parent), "dirs": dirs[:300],
            "files": files[:300], "drives": drives}


def make_server(state: AppState, host: str = "127.0.0.1", port: int = 0):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"SystemRestore/{__version__}"  # HTTP 头只能含 ASCII
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 静默
            pass

        # ---------- 工具 ----------
        def _send_json(self, obj, code: int = 200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, text: str):
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                return {}

        def _ok(self, message="", **extra):
            self._send_json({"ok": True, "message": message, **extra})

        def _err(self, message, code=400):
            self._send_json({"ok": False, "message": message}, code=code)

        # ---------- 路由 ----------
        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                html_path = resource_path("index.html")
                try:
                    self._send_html(html_path.read_text(encoding="utf-8"))
                except OSError:
                    self._send_html("<h3>前端文件缺失</h3>")
                return
            if path == "/api/state":
                self._send_json({"ok": True, "app": __app_name__, "version": __version__,
                                 "stats": state.stats_payload(),
                                 "scan": state.scan_status_payload()})
                return
            if path == "/api/items":
                q = parse_qs(urlparse(self.path).query)
                self._send_json({"items": state.items_payload(
                    q.get("search", [""])[0], q.get("cat", ["全部"])[0],
                    install_order=(q.get("order", [""])[0] == "install"))})
                return
            if path == "/api/scan/status":
                self._send_json(state.scan_status_payload())
                return
            if path == "/api/install/status":
                self._send_json(state.install_status_payload())
                return
            if path == "/api/probe/status":
                self._send_json(state.probe_status_payload())
                return
            if path == "/api/browse":
                q = parse_qs(urlparse(self.path).query)
                exts = tuple(e for e in (q.get("ext", [""])[0] or "").split(",") if e) or (".json",)
                self._send_json(_browse(q.get("path", [""])[0], exts))
                return
            self._err("未知接口", 404)

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._body()
            try:
                if path == "/api/scan":
                    self._ok(state.start_scan(body))
                elif path == "/api/manifest/open":
                    self._ok(state.load_manifest(body.get("path", "")), stats=state.stats_payload())
                elif path == "/api/manifest/save":
                    if not body.get("path"):
                        return self._err("请先填写保存路径")
                    self._ok(state.save_manifest(body["path"]), stats=state.stats_payload())
                elif path == "/api/items":
                    self._ok(_add_item(state, body))
                elif path == "/api/items/update":
                    self._ok(_update_item(state, body))
                elif path == "/api/items/delete":
                    self._ok(_delete_items(state, body))
                elif path == "/api/items/enabled":
                    self._ok(_set_enabled(state, body), stats=state.stats_payload())
                elif path == "/api/items/checkall":
                    self._ok(_check_all(state, body.get("mode", "all")), stats=state.stats_payload())
                elif path == "/api/install/start":
                    self._ok(state.start_install(body.get("keys"), force=bool(body.get("force"))))
                elif path == "/api/install/stop":
                    self._ok(state.stop_install())
                elif path == "/api/install/retry":
                    self._ok(state.retry_failed())
                elif path == "/api/probe":
                    self._ok(state.start_probe())
                elif path == "/api/fixpath":
                    self._ok(_do_fixpath(state))
                elif path == "/api/shutdown":
                    msg = state.request_shutdown()
                    if not msg.startswith("OK"):
                        return self._err(msg.split(":", 1)[1].strip(), 409)
                    self._ok("正在退出工具…")
                    # 响应先回到浏览器，再退出进程（0.5s 足够完成发送）
                    threading.Timer(0.5, lambda: os._exit(0)).start()
                else:
                    self._err("未知接口", 404)
            except Exception as e:  # noqa: BLE001
                self._err(str(e), 500)

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


# ---------------- 业务辅助 ----------------
def _add_item(state: AppState, body: dict) -> str:
    method = body.get("method") or M_MANUAL
    local_file = (body.get("local_file") or "").strip()
    target = (body.get("target") or "").strip()
    if method == M_LOCAL:
        # 本地安装包方式：文件信息统一放 local_file（兼容把文件名填在 target 里的情况）
        if not local_file and target:
            local_file, target = target, ""
        if not local_file:
            raise ValueError("本地安装包方式需要指定安装包文件（.exe/.msi）")
    it = Item(
        name=(body.get("name") or "").strip(),
        version=(body.get("version") or "").strip(),
        publisher=(body.get("publisher") or "").strip(),
        method=method,
        target=target,
        silent_args=(body.get("silent_args") or "").strip(),
        category=body.get("category") or model.CAT_APP,
        enabled=bool(body.get("enabled", True)),
        notes=(body.get("notes") or "").strip(),
        desc=(body.get("desc") or "").strip(),
        local_file=local_file,
        source="manual",
    )
    if not it.name:
        raise ValueError("名称不能为空")
    if not it.desc:  # 用户没填则自动生成用途说明
        it.desc = describe(it.name, it.target, it.category, it.method)
    with state.lock:
        state.manifest.add(it)
    return f"已添加「{it.name}」"


def _update_item(state: AppState, body: dict) -> str:
    idx = int(body.get("index", -1))
    with state.lock:
        if not (0 <= idx < len(state.manifest.items)):
            raise ValueError("条目不存在")
        old = state.manifest.items[idx]
        old.name = (body.get("name") or old.name).strip()
        old.version = (body.get("version") or "").strip()
        old.publisher = (body.get("publisher") or "").strip()
        old.method = body.get("method") or old.method
        old.target = (body.get("target") or "").strip()
        old.silent_args = (body.get("silent_args") or "").strip()
        old.category = body.get("category") or old.category
        old.notes = (body.get("notes") or "").strip()
        old.desc = (body.get("desc") or "").strip()
        old.enabled = bool(body.get("enabled", old.enabled))
        local_file = (body.get("local_file") or "").strip()
        if old.method == M_LOCAL:
            # 本地安装包方式：文件名兼容放在 target 里传的情况
            if not local_file and old.target:
                local_file, old.target = old.target, ""
            if not local_file:
                raise ValueError("本地安装包方式需要指定安装包文件（.exe/.msi）")
        old.local_file = local_file
    return f"已更新「{old.name}」"


def _delete_items(state: AppState, body: dict) -> str:
    indexes = sorted({int(i) for i in body.get("indexes", [])}, reverse=True)
    removed = 0
    with state.lock:
        for i in indexes:
            if 0 <= i < len(state.manifest.items):
                state.manifest.items.pop(i)
                removed += 1
    return f"已删除 {removed} 个条目"


def _set_enabled(state: AppState, body: dict) -> str:
    indexes = [int(i) for i in body.get("indexes", [])]
    enabled = bool(body.get("enabled", True))
    n = 0
    with state.lock:
        for i in indexes:
            if 0 <= i < len(state.manifest.items):
                state.manifest.items[i].enabled = enabled
                n += 1
    return f"已{'勾选' if enabled else '取消'} {n} 个条目"


def _check_all(state: AppState, mode: str) -> str:
    with state.lock:
        for it in state.manifest.items:
            it.enabled = (mode != "none") if mode != "invert" else (not it.enabled)
    return "已更新勾选状态"


def _do_fixpath(state: AppState) -> str:
    with state.lock:
        manifest = state.manifest
        items = [it for it in manifest.items if it.enabled]
    msgs = []
    # 1) 按本机已安装的开发工具自动补 PATH / JAVA_HOME（目录不存在则不写，安全）
    try:
        r = ensure_environment(items)
        if r["added"] or r["env"]:
            msgs.append(f"已补齐 {len(r['added'])} 个 PATH 条目"
                        + (f"，设置 {', '.join(r['env'])}" if r["env"] else ""))
        else:
            msgs.append("各工具目录均已在 PATH 中")
    except Exception as e:  # noqa: BLE001
        msgs.append(f"自动配置失败: {e}")
    # 2) 清单快照中的用户 PATH（只增不删）
    try:
        msgs.append(fix_user_path(manifest))
    except Exception as e:  # noqa: BLE001
        msgs.append(f"快照补齐失败: {e}")
    return "；".join(msgs)


def start(port: int | None = None, open_browser: bool = True, host: str = "127.0.0.1"):
    """启动本地服务并打开浏览器，阻塞直到 Ctrl+C。"""
    state = AppState()
    httpd = make_server(state, host=host, port=port or 0)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}"
    print(f"{__app_name__} v{__version__}")
    print(f"本地服务已启动: {url}")
    print("在浏览器中操作即可；点页面右上角「退出工具」、关闭此窗口或按 Ctrl+C 均可退出。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0
