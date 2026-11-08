from __future__ import annotations

import argparse
import importlib.resources
import json
import math
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__, config, downloads, runtime, telemetry

ACTIONS = ("start", "stop", "restart", "status", "codex")
COMMANDS = {*ACTIONS, "init", "list", "logs", "log", "config", "monitor", "speed", "download", "web", "urls"}


def normalize(argv: list[str]) -> list[str]:
    """Keep `llm model engine action` compatible with action-first commands."""
    prefix = []
    rest = list(argv)
    while rest:
        if rest[0] in ("--config", "-c") and len(rest) >= 2:
            prefix += rest[:2]
            rest = rest[2:]
        elif rest[0] == "--dry-run":
            prefix.append(rest.pop(0))
        else:
            break
    if len(rest) >= 3 and rest[0] not in COMMANDS and not rest[0].startswith("-"):
        name, engine, action, *extra = rest
        if action in COMMANDS:
            if action in ("log", "logs") and not any(s in extra for s in ("-f", "--follow", "--no-follow")):
                extra.append("-f")
            rest = [action, name, engine, *extra]
    return prefix + rest


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="llm", description="本地模型管理：程序、配置、启停、日志、监控与下载进度。")
    p.add_argument("--version", action="version", version="llm-manager "+__version__)
    p.add_argument("-c", "--config", help="配置文件路径；也可设置 LLM_CONFIG")
    p.add_argument("--dry-run", action="store_true", help="仅显示执行计划")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="创建示例配置，不覆盖已有文件")
    ls = sub.add_parser("list", help="列出已配置模型")
    ls.add_argument("--json", action="store_true")
    for action in ACTIONS:
        q = sub.add_parser(action, help=f"执行目标的 {action} 命令")
        q.add_argument("name", nargs="?" if action == "status" else None)
        q.add_argument("engine", nargs="?")
        q.add_argument("extra", nargs=argparse.REMAINDER, help="原样传给程序的额外参数")
    q = sub.add_parser("logs", aliases=["log"], help="查看模型或服务日志")
    q.add_argument("target", nargs="*")
    q.add_argument("-n", "--lines", type=int, default=100)
    g = q.add_mutually_exclusive_group()
    g.add_argument("-f", "--follow", action="store_true")
    g.add_argument("--no-follow", action="store_false", dest="follow")
    q.add_argument("--context")
    q.add_argument("--host", action="store_true")
    q.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    q = sub.add_parser("config", help="查看配置文件、程序和模型路径")
    q.add_argument("target", nargs="*")
    q.add_argument("--context")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("monitor", aliases=["speed"], help="实时SGLang吞吐与GPU监控，不发送生成请求")
    q.add_argument("target", nargs="*")
    q.add_argument("--once", action="store_true")
    q.add_argument("--json", action="store_true")
    q.add_argument("--no-gpu", action="store_true", help="不采集GPU，避免硬件轮询")
    q.add_argument("-i", "--interval", type=float, default=2)
    q.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    q = sub.add_parser("download", help="查看已有aria2任务的下载进度，不发起下载")
    q.add_argument("target", nargs="*")
    q.add_argument("-f", "--follow", "--watch", action="store_true")
    q.add_argument("--json", action="store_true")
    q.add_argument("-i", "--interval", type=float, default=2)
    q.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    q = sub.add_parser("web", aliases=["urls"], help="查看网页地址与可访问状态")
    q.add_argument("--json", action="store_true")
    q.add_argument("--no-check", action="store_true", help="只显示地址，不发送HTTP请求")
    return p


def watch(make, render, *, interval, once, as_json):
    if not math.isfinite(interval) or not 0.5 <= interval <= 60:
        raise config.ConfigError("刷新间隔必须在0.5到60秒之间。")
    screen = sys.stdout.isatty() and not once and not as_json
    try:
        if screen:
            print("\033[?25l", end="", flush=True)
        while True:
            value = make()
            if screen:
                print("\033[2J\033[H", end="")
            print(json.dumps(value, ensure_ascii=False) if as_json else render(value), flush=True)
            if once:
                break
            time.sleep(interval)
    finally:
        if screen:
            print("\033[?25h", end="", flush=True)


def expand_tree(value, values):
    if isinstance(value, str):
        return config.expand(value, values)
    if isinstance(value, dict):
        return {k: expand_tree(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_tree(v, values) for v in value]
    return value


def config_view(data, args):
    result = {"config_file": data["_path"], "python": sys.executable, "package": str(Path(__file__).parent), "version": __version__}
    if args.target:
        entry = config.resolve(data, args.target)
        if args.context and args.context not in entry.get("contexts", []):
            raise config.ConfigError("此目标不支持该 --context。")
        values = config.variables(data, entry, {"context": args.context} if args.context else None)
        result["target"] = config.redacted(expand_tree(entry, values))
        paths = result["target"].get("paths", {})
        result["path_status"] = {name: {"path": str(path), "exists": Path(str(path)).exists(), "resolved_path": str(Path(str(path)).resolve())} for name, path in paths.items()}
    else:
        result["models"] = [[m["name"], m["engine"]] for m in data.get("models", [])]
        result["services"] = [s["name"] for s in data.get("services", [])]
        result["downloads"] = [s["name"] for s in data.get("downloads", [])]
    print(json.dumps(result, ensure_ascii=False, indent=2))


def web(data, args):
    values = config.variables(data, {})
    result = []
    for page in data.get("web", []):
        row = {"name": page["name"], "url": config.expand(page["url"], values), "reachable": None}
        if not args.no_check:
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(row["url"], timeout=2) as response:
                    row["reachable"] = response.status == 200
            except OSError:
                row["reachable"] = False
        result.append(row)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for row in result:
            state = "可访问" if row["reachable"] else "未能连接" if row["reachable"] is False else "未检查"
            print(f"{row['name']}\n  {row['url']} [{state}]")
        print("网页可访问不代表模型已就绪。")


def execute(args):
    if args.command == "init":
        path = Path(args.config).expanduser() if args.config else config.default_path()
        if path.exists():
            raise config.ConfigError(f"配置已存在，未覆盖：{path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        example = importlib.resources.files("llm_manager").joinpath("example.json").read_text(encoding="utf-8")
        # Exclusive create also protects against a concurrent initializer.
        with path.open("x", encoding="utf-8") as f:
            f.write(example)
        print(f"已创建：{path}\n请先修改模型路径、服务名和API地址，然后运行 llm list。")
        return 0
    data = config.load(args.config)
    if args.command == "list":
        rows = [{"model": m["name"], "engine": m["engine"], "description": m.get("description", ""), "actions": list(m.get("commands", {}))} for m in data.get("models", [])]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                print(f"{row['model']:14} {row['engine']:22} {row['description']} [{', '.join(row['actions'])}]")
            print("\nllm config · llm logs · llm monitor · llm download · llm web")
        return 0
    if args.command in ACTIONS:
        if args.command == "status" and args.name is None:
            for entry in data.get("models", []):
                if entry.get("api"):
                    online, _ = runtime.probe(data, entry)
                    print(entry["name"], entry["engine"], "API在线" if online else "API离线")
            return 0
        target = [args.name] + ([args.engine] if args.engine else [])
        entry = config.resolve(data, target)
        extra = list(args.extra)
        if extra[:1] == ["--"]:
            extra = extra[1:]
        dry = args.dry_run or "--dry-run" in extra
        extra = [s for s in extra if s != "--dry-run"]
        return runtime.run(data, entry, args.command, extra, dry)
    if args.command in ("logs", "log"):
        if not args.target:
            print("llm logs 模型 引擎 [-n 100] [-f]\nllm logs 服务名 [-n 100] [-f]")
            for item in [*data.get("models", []), *data.get("services", [])]:
                if item.get("logs"):
                    print(item["name"], item.get("engine", ""))
            return 0
        if not 1 <= args.lines <= 100000:
            raise config.ConfigError("行数必须在1到100000之间。")
        entry = config.resolve(data, args.target)
        argv = runtime.log_command(data, entry, args.lines, args.follow, args.context, args.host)
        if args.dry_run:
            print(shlex.join(argv))
            return 0
        return subprocess.call(argv)
    if args.command == "config":
        config_view(data, args)
        return 0
    if args.command in ("monitor", "speed"):
        target = list(args.target)
        if len(target) == 1 and any([m["name"], m["engine"]] == [target[0], "sglang"] for m in data.get("models", [])):
            target.append("sglang")
        entry = config.resolve(data, target, service=False, default=True)
        if args.dry_run:
            print(json.dumps(config.redacted(entry.get("monitor", {})), ensure_ascii=False))
            return 0
        watch(lambda: telemetry.snapshot(data, entry, no_gpu=args.no_gpu), telemetry.render, interval=args.interval, once=args.once, as_json=args.json)
        return 0
    if args.command == "download":
        items = downloads.select(data, args.target)
        if args.dry_run:
            print(json.dumps(config.redacted(items), ensure_ascii=False, indent=2))
            return 0
        watch(lambda: [downloads.snapshot(data, item) for item in items], downloads.render, interval=args.interval, once=not args.follow, as_json=args.json)
        return 0
    if args.command in ("web", "urls"):
        web(data, args)
        return 0
    raise config.ConfigError("未知命令。")


def main(argv=None):
    try:
        args = parser().parse_args(normalize(list(sys.argv[1:] if argv is None else argv)))
        return execute(args)
    except (config.ConfigError, OSError) as exc:
        print(f"llm: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
