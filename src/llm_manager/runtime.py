from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import urllib.request
from pathlib import Path

from .config import ConfigError, expand, variables, redacted


def command(data: dict, target: dict, action: str, extra: list[str] | None = None) -> tuple[list[str], dict, str | None]:
    spec = target.get("commands", {}).get(action)
    if spec is None:
        raise ConfigError(f"目标不支持 {action}；可用动作：{', '.join(target.get('commands', {}))}")
    if isinstance(spec, list):
        spec = {"argv": spec}
    if not isinstance(spec, dict) or not isinstance(spec.get("argv"), list) or not spec["argv"] or not all(isinstance(s, str) for s in spec["argv"]):
        raise ConfigError("命令必须为非空参数数组，或包含 argv 数组的对象；不接受shell字符串。")
    values = variables(data, target)
    argv = [expand(s, values) for s in spec["argv"]] + list(extra or [])
    env = os.environ.copy()
    additions = spec.get("env", {})
    if not isinstance(additions, dict):
        raise ConfigError("命令 env 必须为对象。")
    env.update({k: expand(str(v), values) for k, v in additions.items()})
    cwd = expand(spec["cwd"], values) if spec.get("cwd") else None
    return argv, env, cwd


def run(data: dict, target: dict, action: str, extra: list[str], dry_run=False) -> int:
    argv, env, cwd = command(data, target, action, extra)
    if dry_run:
        print(shlex.join(redacted(argv)))
        return 0
    if not shutil.which(argv[0]):
        raise ConfigError(f"程序不可执行或未安装：{argv[0]}")
    return subprocess.call(argv, env=env, cwd=cwd)


def log_command(data: dict, target: dict, lines=100, follow=False, context=None, host=False) -> list[str]:
    spec = target.get("logs")
    if not isinstance(spec, dict):
        raise ConfigError("此目标尚未配置 logs。")
    if context is not None and context not in target.get("contexts", []):
        raise ConfigError("此目标不支持该 --context。")
    values = variables(data, target, {"context": context} if context else None)
    if spec.get("type") == "file":
        key = "host_path" if host else "path"
        if key not in spec:
            raise ConfigError(f"日志配置缺少 {key}。")
        path = expand(spec[key], values)
        return ["tail", "-n", str(lines), *(["-F"] if follow else []), path]
    if spec.get("type") == "journal":
        if host:
            raise ConfigError("--host 仅适用于带 host_path 的文件日志。")
        return ["journalctl", "--user", "-u", expand(spec["unit"], values), "-n", str(lines), "--no-pager", "-o", "short-iso", *(["-f"] if follow else [])]
    raise ConfigError("logs.type 仅支持 file 或 journal。")


def fetch_json(url: str, *, payload=None, timeout=2, api_key_env=None):
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        key = os.environ.get(api_key_env)
        if not key:
            raise ConfigError(f"未设置环境变量：{api_key_env}")
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


def probe(data: dict, target: dict) -> tuple[bool, str | None]:
    spec = target.get("api", {})
    if not spec.get("base_url"):
        return False, None
    url = expand(spec["base_url"], variables(data, target)).rstrip("/") + "/models"
    try:
        result = fetch_json(url, api_key_env=spec.get("api_key_env"))
        models = result.get("data", [])
        return True, models[0].get("id") if models else None
    except (OSError, ValueError, KeyError):
        return False, None
