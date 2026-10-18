#!/usr/bin/env python3
"""系统复原工具 SystemRestore - 入口。

用法：
  python run.py                      # 启动本地 Web 界面（默认，自动打开浏览器）
  python run.py --scan out.json      # 扫描本机并保存清单（--local-dir 指定本地安装包目录）
  python run.py --install m.json     # 按清单一键安装（可加 --dry-run 只打印命令）
  python run.py --selftest           # 服务端冒烟测试
  python run.py --port 8600          # 指定 Web 服务端口
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sysrestore import __app_name__, __version__


def cli_scan(args) -> int:
    from sysrestore import system
    from sysrestore.model import Manifest
    from sysrestore.scanner import ScanOptions, Scanner

    out = Path(args.scan).expanduser().resolve()
    base = out.parent
    backup = None if args.no_configs else (base / "configs")
    local_dir = (Path(args.local_dir).expanduser() if args.local_dir
                 else (base / "packages"))

    opts = ScanOptions(
        store=not args.no_store,
        wsl=not args.no_wsl,
        npm=not args.no_npm,
        pip=not args.no_pip,
        configs=not args.no_configs,
        inbox=not args.with_inbox,
        local=not args.no_local,
        local_dir=local_dir,
        backup_dir=backup,
    )
    sc = Scanner(
        progress=lambda step, i, n, msg: print(f"[{min(i + 1, n)}/{n}] {msg}"),
        log=lambda m: print("      -", m),
    )
    m = sc.scan(opts)
    m.save(out, config_dir_relative_to=base)

    print()
    print(f"清单已保存: {out}")
    if backup:
        print(f"配置备份目录: {backup}")
    st = m.stats()
    n_inbox = int(m.meta.get("inbox_filtered", 0) or 0)
    if n_inbox:
        print(f"已自动排除 {n_inbox} 个系统自带组件（Edge、OneDrive、Windows 内置应用等；"
              f"加 --with-inbox 可改为保留）")
    n_local = sum(1 for it in m.items if it.local_file)
    if not args.no_local:
        print(f"本地安装包目录: {local_dir}"
              + (f"（{n_local} 个条目带本地安装包，安装时优先本地）" if n_local else "（无可用安装包）"))
    print(f"共 {st['total']} 项（勾选 {st['enabled']}）：")
    for k, v in sorted(st["by_category"].items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")
    print("\n明细（前 50 项）：")
    for it in m.items[:50]:
        print(f"    [{it.category}] {it.name} {it.version}  <{it.method_name()}> {it.target}"
              + (f"  [本地包: {it.local_file}]" if it.local_file else ""))
    if len(m.items) > 50:
        print(f"    ... 其余 {len(m.items) - 50} 项见清单文件")
    return 0


def cli_install(args) -> int:
    from sysrestore.installer import Installer
    from sysrestore.model import Manifest

    m = Manifest.load(args.install)
    summary_holder = {}

    inst = Installer(
        m, Path(args.install),
        dry_run=args.dry_run,
        force=args.force,
        on_log=lambda msg: print(msg),
        on_item=lambda it, st, d: print(f"  [{st}] {it.name} {it.version} ({it.method_name()})"
                                        + (f" - {d}" if d else "")),
        on_progress=lambda d, t: print(f"  进度 {d}/{t}"),
        on_finish=lambda s, r: summary_holder.update(s),
    )
    inst.run()
    print()
    print(f"安装结束: 成功 {summary_holder.get('ok', 0)}, 失败 {summary_holder.get('failed', 0)}, "
          f"需手动 {summary_holder.get('manual', 0)}, 已存在跳过 {summary_holder.get('exists', 0)}, "
          f"跳过 {summary_holder.get('skipped', 0)}")
    if inst.report_path:
        print(f"报告: {inst.report_path}")
    return 0 if summary_holder.get("failed", 0) == 0 else 1


def selftest() -> int:
    """启动本地服务，逐个调用 API 验证。"""
    from sysrestore.webui import AppState, make_server

    state = AppState()
    httpd = make_server(state, port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 不走代理

    def get(path):
        with opener.open(base + path, timeout=15) as r:
            return r.read().decode("utf-8")

    def post(path, obj):
        req = urllib.request.Request(base + path, data=json.dumps(obj).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        print(f"  [{'OK' if cond else 'FAIL'}] {name}")

    try:
        html = get("/")
        check("GET / 返回前端页面", "<title>系统复原工具" in html)
        state_json = json.loads(get("/api/state"))
        check("GET /api/state 返回 ok", state_json.get("ok") is True and "stats" in state_json)
        check("默认清单目录为桌面\\SystemRestore",
              str(state_json["stats"].get("default_dir", "")).replace("/", "\\").endswith("Desktop\\SystemRestore"))
        check("GET /api/items 初始为空", json.loads(get("/api/items"))["items"] == [])
        r = post("/api/items", {"name": "测试软件", "version": "1.0", "method": "winget",
                                "target": "Git.Git", "category": "开发工具"})
        check("POST /api/items 新增", r.get("ok"))
        items = json.loads(get("/api/items"))["items"]
        check("新增后列表为 1 项", len(items) == 1)
        r = post("/api/items/update", {"index": 0, "name": "测试软件改", "method": "winget",
                                       "target": "Git.Git", "category": "开发工具"})
        check("POST /api/items/update 编辑", r.get("ok"))
        items = json.loads(get("/api/items"))["items"]
        check("编辑生效", items[0]["name"] == "测试软件改")
        post("/api/items/enabled", {"indexes": [0], "enabled": False})
        check("取消勾选生效", json.loads(get("/api/items"))["items"][0]["enabled"] is False)
        post("/api/items/checkall", {"mode": "all"})
        check("全选生效", json.loads(get("/api/items"))["items"][0]["enabled"] is True)
        browse = json.loads(get("/api/browse?path="))
        check("GET /api/browse", bool(browse.get("path")))
        post("/api/items/delete", {"indexes": [0]})
        check("删除后列表为空", json.loads(get("/api/items"))["items"] == [])
        # ---- 本地安装包 ----
        r = post("/api/items", {"name": "本地测试软件", "method": "local",
                                "target": "demo-setup.exe", "category": "应用软件"})
        check("POST /api/items 新增本地安装包条目", r.get("ok"))
        items = json.loads(get("/api/items"))["items"]
        check("本地安装包条目字段往返（method/local_file/中文名）",
              bool(items) and items[0]["method"] == "local"
              and items[0]["local_file"] == "demo-setup.exe"
              and items[0]["method_name"] == "本地安装包")
        try:
            r_bad = post("/api/items", {"name": "坏条目", "method": "local"})
            rejected = not r_bad.get("ok")
        except Exception:
            rejected = True  # HTTP 400 同样是拒绝
        check("本地安装包方式未指定文件时拒绝", rejected)
        r = post("/api/items/update", {"index": 0, "name": "本地测试软件改", "method": "local",
                                       "target": "demo-setup.exe", "category": "应用软件"})
        check("本地安装包条目可编辑", r.get("ok"))
        st_local = json.loads(get("/api/install/status"))
        loc = st_local.get("local") or {}
        check("安装状态含本地包就绪状态（文件不存在时 found=False）",
              isinstance(loc, dict)
              and any(v.get("file") == "demo-setup.exe" and v.get("found") is False
                      for v in loc.values()))
        browse2 = json.loads(get("/api/browse?path=&ext=.exe,.msi"))
        check("GET /api/browse 支持按扩展名过滤", bool(browse2.get("path")))
        post("/api/items/delete", {"indexes": [0]})
        check("清理后列表为空", json.loads(get("/api/items"))["items"] == [])
        # 环境变量自动配置：空清单 / 未安装工具均不得写入
        from sysrestore.installer import ensure_environment
        from sysrestore.model import Item
        r = ensure_environment([])
        check("ensure_environment 空清单安全返回",
              r == {"added": [], "env": {}, "tools": []})
        r2 = ensure_environment([Item(name="不存在的工具XYZ", method="winget",
                                      target="Nope.Nope", category="开发工具")])
        check("ensure_environment 未安装工具不写环境变量",
              r2["added"] == [] and r2["env"] == {})
        # 本地安装包解析（临时目录 + 假安装包文件）
        import tempfile
        from pathlib import Path as _Path
        from sysrestore.installer import resolve_local_installer
        from sysrestore.model import Manifest as _Manifest
        with tempfile.TemporaryDirectory() as td:
            f = _Path(td) / "demo-setup.exe"
            f.write_bytes(b"x")
            check("resolve_local_installer 目录+文件名可找到",
                  resolve_local_installer("demo-setup.exe", td, None) == f)
            check("resolve_local_installer 绝对路径直查",
                  resolve_local_installer(str(f), None, None) == f)
            check("resolve_local_installer 找不到返回 None",
                  resolve_local_installer("nope.exe", td, None) is None)
            mm = _Manifest()
            mm.meta["local_dir"] = td
            check("Manifest.local_dir 取 meta 记录的目录", mm.local_dir(None) == _Path(td))
    finally:
        httpd.shutdown()
        httpd.server_close()

    failed = [n for n, ok in checks if not ok]
    print(f"\nselftest: {len(checks) - len(failed)}/{len(checks)} 通过")
    if failed:
        print("失败项:", failed)
        return 1
    print("服务端冒烟测试全部通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description=f"{__app_name__} v{__version__}：重装系统前扫描软件与开发环境，重装后一键复原",
    )
    parser.add_argument("--scan", metavar="OUT.json", help="扫描本机并保存清单到指定路径")
    parser.add_argument("--install", metavar="MANIFEST.json", help="按清单一键安装")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不实际安装")
    parser.add_argument("--force", action="store_true", help="安装时跳过系统现状检测，强制重装所有勾选项")
    parser.add_argument("--no-store", action="store_true", help="扫描时跳过商店应用")
    parser.add_argument("--no-wsl", action="store_true", help="扫描时跳过 WSL")
    parser.add_argument("--no-npm", action="store_true", help="扫描时跳过 npm 全局包")
    parser.add_argument("--no-pip", action="store_true", help="扫描时跳过 pip 包")
    parser.add_argument("--no-configs", action="store_true", help="扫描时跳过配置文件备份")
    parser.add_argument("--with-inbox", action="store_true",
                        help="扫描时保留系统自带组件（Edge/OneDrive/Windows 内置应用等，默认排除）")
    parser.add_argument("--local-dir", metavar="DIR", default="",
                        help="本地安装包目录（默认：清单所在目录下的 packages 文件夹；放入 .exe/.msi 后扫描自动关联）")
    parser.add_argument("--no-local", action="store_true", help="扫描时跳过本地安装包目录")
    parser.add_argument("--selftest", action="store_true", help="服务端冒烟测试")
    parser.add_argument("--port", type=int, default=0, help="Web 服务端口（默认自动分配）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--version", action="store_true", help="显示版本")
    args = parser.parse_args()

    if args.version:
        print(f"{__app_name__} v{__version__}")
        return 0
    if args.scan and args.install:
        parser.error("--scan 和 --install 不能同时使用")
    if args.scan:
        return cli_scan(args)
    if args.install:
        return cli_install(args)
    if args.selftest:
        return selftest()

    from sysrestore import webui
    return webui.start(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    sys.exit(main())
