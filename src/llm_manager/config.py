from __future__ import annotations

import json
import os
import re
from pathlib import Path
from string import Template


class ConfigError(ValueError):
    pass


def default_path() -> Path:
    if os.environ.get("LLM_CONFIG"):
        return Path(os.environ["LLM_CONFIG"]).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "llm-manager" / "config.json"


def load(path: str | Path | None = None) -> dict:
    path = Path(path).expanduser() if path else default_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"配置不存在：{path}；请先运行 llm init。") from None
    except (OSError, ValueError) as exc:
        raise ConfigError(f"读取配置失败：{exc}") from None
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ConfigError("配置必须为JSON对象，并设置 version: 1。")
    for name in ("models", "services", "downloads", "web"):
        if not isinstance(data.get(name, []), list):
            raise ConfigError(f"{name} 必须为数组。")
    identities = set()
    for model in data.get("models", []):
        if not isinstance(model, dict) or not all(isinstance(model.get(k), str) and model[k] for k in ("name", "engine")):
            raise ConfigError("每个模型必须提供非空 name 和 engine。")
        identity = (model["name"], model["engine"])
        if identity in identities:
            raise ConfigError(f"重复模型：{' / '.join(identity)}")
        identities.add(identity)
        if not isinstance(model.get("commands", {}), dict):
            raise ConfigError("commands 必须为对象。")
    for kind in ("services", "downloads"):
        names = []
        for entry in data.get(kind, []):
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
                raise ConfigError(f"{kind} 条目必须提供非空 name。")
            names.append(entry["name"])
        if len(names) != len(set(names)):
            raise ConfigError(f"{kind} 中存在重复名称。")
    for item in data.get("downloads", []):
        if not isinstance(item.get("file"), str) or not item["file"]:
            raise ConfigError("每个下载任务必须配置 file。")
    data["_path"] = str(path.resolve())
    return data


def resolve(data: dict, target: list[str], *, service: bool = True, default: bool = False) -> dict:
    if not target and default:
        target = list(data.get("default_monitor", []))
        if not target and len(data.get("models", [])) == 1:
            target = [data["models"][0]["name"], data["models"][0]["engine"]]
    if len(target) == 2:
        for model in data.get("models", []):
            if [model["name"], model["engine"]] == target:
                return model
    if service and len(target) == 1:
        for entry in data.get("services", []):
            if target[0] in [entry["name"], *entry.get("aliases", [])]:
                return entry
    raise ConfigError("未知或缺失目标；运行 llm list 查看模型，llm config 查看配置位置。")


def variables(data: dict, target: dict, overrides: dict | None = None) -> dict:
    values = {
        "home": str(Path.home()),
        "config_dir": str(Path(data["_path"]).parent),
        "model": target.get("name", ""),
        "engine": target.get("engine", ""),
        **data.get("variables", {}),
        **target.get("variables", {}),
        **(overrides or {}),
    }
    for _ in range(10):
        updated = {k: Template(str(v)).safe_substitute(values) for k, v in values.items()}
        if updated == values:
            break
        values = updated
    return values


def expand(value: str, values: dict) -> str:
    def replace(match):
        key = match[1]
        if key not in values:
            raise ConfigError(f"配置变量未定义：{key}")
        return str(values[key])
    result = re.sub(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}", replace, value)
    if "${" in result:
        raise ConfigError(f"配置存在未解析或循环变量：{value!r}")
    return os.path.expanduser(result)


def redacted(value):
    if isinstance(value, dict):
        return {k: "***" if re.search(r"(^|_)(password|secret|token|api_key|authorization)($|_)", k, re.I) and not k.endswith("_env") else redacted(v) for k, v in value.items()}
    if isinstance(value, list):
        result = []
        hide_next = False
        for item in value:
            if hide_next:
                result.append("***")
                hide_next = False
            else:
                result.append(redacted(item))
                hide_next = isinstance(item, str) and item in ("--api-key", "--password", "--token", "--secret")
        return result
    if isinstance(value, str):
        value = re.sub(r"(?i)(Bearer\s+)\S+", r"\1***", value)
        value = re.sub(r"(?i)(--(?:api-key|password|token|secret)=)\S+", r"\1***", value)
    return value
