from __future__ import annotations

from pathlib import Path

from .config import ConfigError, expand, variables
from .runtime import fetch_json


def select(data: dict, target: list[str]) -> list[dict]:
    if target[:1] == ["status"]:
        target = target[1:]
    items = data.get("downloads", [])
    if not target:
        return items
    found = [d for d in items if target == [d["name"]] or target == [d.get("model"), d.get("engine")]]
    if not found:
        raise ConfigError("没有此下载任务；运行 llm download 查看已登记任务。")
    return found


def snapshot(data: dict, item: dict) -> dict:
    values = variables(data, item)
    path = Path(expand(item["file"], values))
    total = item.get("total_bytes", 0)
    if not isinstance(total, int) or total < 0:
        raise ConfigError("download.total_bytes 必须为非负整数。")
    result = {"name": item["name"], "model": item.get("model", item["name"]), "completed_bytes": 0, "total_bytes": total, "download_bytes_per_second": 0, "state": "尚无本地文件", "source": "磁盘估算", "verified": False, "file": str(path)}
    rpc = item.get("rpc_url")
    if rpc:
        params = [["completedLength", "totalLength", "downloadSpeed", "files"]]
        if item.get("rpc_secret_env"):
            import os
            secret = os.environ.get(item["rpc_secret_env"])
            if not secret:
                raise ConfigError(f"未设置环境变量：{item['rpc_secret_env']}")
            params.insert(0, "token:"+secret)
        payload = {"jsonrpc": "2.0", "id": "llm-manager", "method": "aria2.tellActive", "params": params}
        try:
            rows = fetch_json(expand(rpc, values), payload=payload)["result"]
            row = next((r for r in rows if any(Path(f.get("path", "")) == path for f in r.get("files", []))), None)
            if row:
                result.update(completed_bytes=int(row["completedLength"]), total_bytes=int(row["totalLength"]), download_bytes_per_second=int(row["downloadSpeed"]), state="下载中", source="aria2实时进度")
                if result["total_bytes"] and result["completed_bytes"] >= result["total_bytes"]:
                    result["state"] = "下载完成，校验/收尾中"
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if result["source"] == "磁盘估算" and path.exists():
        stat = path.stat()
        size = getattr(stat, "st_blocks", 0)*512 if hasattr(stat, "st_blocks") else stat.st_size
        result["completed_bytes"] = min(total, size) if total else size
        result["state"] = "无活动下载（磁盘估算），待校验"
    if item.get("verified_marker") and Path(expand(item["verified_marker"], values)).is_file():
        result.update(verified=True, state="权重校验标记已存在", source="校验标记")
        result["completed_bytes"] = result["total_bytes"]
    speed = result["download_bytes_per_second"]
    result["eta_seconds"] = max(0, round((result["total_bytes"]-result["completed_bytes"])/speed)) if speed and result["total_bytes"] else None
    return result


def render(data: list[dict]) -> str:
    if not data:
        return "尚未登记下载任务；在配置文件 downloads 中设置 file、rpc_url、total_bytes。"
    rows = []
    for item in data:
        total = item["total_bytes"]
        percent = min(100, 100*item["completed_bytes"]/total) if total else None
        eta = item["eta_seconds"]
        eta_text = f"{eta//60}分{eta%60}秒" if eta is not None else "—"
        rows.extend([f"{item['name']} · {item['state']}", f"进度：{percent:.1f}%" if percent is not None else "进度：总量未知", f"已完成：{item['completed_bytes']/1e9:.2f} / {total/1e9:.2f} GB", f"下载速度：{item['download_bytes_per_second']/2**20:.2f} MiB/s    预计剩余：{eta_text}", f"来源：{item['source']}    文件：{item['file']}", ""])
    rows.append("只查询已登记的下载任务，不发起下载。磁盘占用不是校验结果；Ctrl+C不停止下载。")
    return "\n".join(rows)
