from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import ConfigError, expand, variables
from .runtime import probe


def parse_sglang(text: str) -> list[dict]:
    """Read batch throughput; never infer exact live request state from silence."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if "server_args=" in line]
    if starts:
        lines = lines[starts[-1]:]
    points = []
    for line in lines:
        kind = "decode" if "Decode batch," in line else "prefill" if "Prefill batch," in line else None
        if not kind:
            continue
        stamp = re.search(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]", line)
        if not stamp:
            continue
        try:
            timestamp = dt.datetime.strptime(stamp[1], "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        def number(key):
            match = re.search(re.escape(key) + r": ([\d.]+)", line)
            return float(match[1]) if match else None
        speed = number("gen throughput (token/s)" if kind == "decode" else "input throughput (token/s)")
        if speed is None:
            continue
        points.append({"kind": kind, "timestamp": timestamp, "time": stamp[1], "speed": speed, "running": number("#running-req"), "queued": number("#queue-req")})
    return points


def tail_text(path: str | Path, limit=1024 * 1024) -> str:
    try:
        with Path(path).open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size-limit)
            f.seek(start)
            content = f.read(limit).decode(errors="replace")
            return content.split("\n", 1)[-1] if start else content
    except FileNotFoundError:
        return ""


def summarize(points: list[dict], online: bool, now=None, stale_after=15) -> dict:
    now = time.time() if now is None else now
    def metric(kind):
        point = next((p for p in reversed(points) if p["kind"] == kind), None)
        age = now-point["timestamp"] if point else None
        fresh = point is not None and online and -1 <= age <= stale_after
        return {
            "tokens_per_second": point["speed"] if fresh else None,
            "last_tokens_per_second": point["speed"] if point else None,
            "age_seconds": round(age, 1) if age is not None else None,
            "sample_time": point["time"] if point else None,
        }
    latest = points[-1] if points else None
    recent = latest is not None and online and -1 <= now-latest["timestamp"] <= stale_after
    return {
        "time": dt.datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "online": online,
        "status": "采样更新中" if recent else "API在线，无近期采样" if online else "API离线",
        "decode": metric("decode"), "prefill": metric("prefill"),
        "running_sample": latest["running"] if recent else None,
        "queued_sample": latest["queued"] if recent else None,
    }


def gpu_stats(sysfs=Path("/sys/class/drm"), nvidia=True) -> list[dict]:
    result = []
    seen = set()
    for device in Path(sysfs).glob("card[0-9]*/device"):
        try:
            identity = str(device.resolve())
            if identity in seen or (device/"vendor").read_text().strip() != "0x1002":
                continue
            seen.add(identity)
            row = {"vendor": "AMD", "name": device.parent.name, "busy_percent": int((device/"gpu_busy_percent").read_text())}
            for source, key in [("mem_info_vram_used", "vram_used_mib"), ("mem_info_vram_total", "vram_total_mib")]:
                row[key] = round(int((device/source).read_text())/2**20, 1)
            for hw in (device/"hwmon").glob("hwmon*"):
                for source, key, divisor in [("power1_average", "power_w", 1e6), ("temp1_input", "temperature_c", 1e3), ("fan1_input", "fan_rpm", 1)]:
                    if (hw/source).exists():
                        row[key] = round(int((hw/source).read_text())/divisor, 1)
            result.append(row)
        except (OSError, ValueError):
            continue
    if nvidia and shutil.which("nvidia-smi"):
        try:
            fields = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
            p = subprocess.run(["nvidia-smi", "--query-gpu="+fields, "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
            if p.returncode == 0:
                for line in p.stdout.splitlines():
                    parts = [s.strip() for s in line.split(",")]
                    if len(parts) != 6:
                        continue
                    row = {"vendor": "NVIDIA", "name": parts[0]}
                    for key, value in zip(("busy_percent", "vram_used_mib", "vram_total_mib", "temperature_c", "power_w"), parts[1:]):
                        try:
                            row[key] = float(value)
                        except ValueError:
                            pass
                    result.append(row)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return result


def snapshot(data: dict, target: dict, *, no_gpu=False) -> dict:
    spec = target.get("monitor", {})
    if spec.get("type") != "sglang-log" or not spec.get("path"):
        raise ConfigError("此目标未配置 SGLang 日志监控；请设置 monitor.type=sglang-log 和 monitor.path。")
    path = expand(spec["path"], variables(data, target))
    online, model = probe(data, target)
    result = summarize(parse_sglang(tail_text(path)), online, stale_after=spec.get("stale_after", 15))
    result.update(model=model or target["name"], engine=target["engine"], source=path)
    result["gpus"] = [] if no_gpu else gpu_stats()
    return result


def render(data: dict) -> str:
    rows = [f"{data['model']} / {data['engine']} · {data['time']}", f"状态：{data['status']}", ""]
    for key, name in [("decode", "生成"), ("prefill", "输入")]:
        value = data[key]
        current = f"{value['tokens_per_second']:.2f} tokens/s" if value["tokens_per_second"] is not None else "—"
        history = f"{value['last_tokens_per_second']:.2f} tokens/s，{value['age_seconds']:.0f}秒前" if value["last_tokens_per_second"] is not None else "尚无采样"
        rows.append(f"{name}吞吐：{current}    最近记录：{history}")
    def count(value):
        return "—" if value is None else str(int(value))
    rows.append(f"最近采样运行/排队：{count(data['running_sample'])} / {count(data['queued_sample'])}")
    for gpu in data["gpus"]:
        rows.append("")
        rows.append(f"{gpu['vendor']} {gpu['name']}")
        labels = [("busy_percent", "占用", "%"), ("vram_used_mib", "显存", "MiB"), ("power_w", "功耗", "W"), ("temperature_c", "温度", "°C"), ("fan_rpm", "风扇", "RPM")]
        rows.append("    ".join(f"{label}：{gpu[key]:g}{unit}" for key, label, unit in labels if key in gpu))
    rows.extend(["", "速度来自SGLang批次日志，含思考token；不是单会话平均值。", "离线/过期数据不显示为当前速度。请求数为最近采样，日志时间按本机时区解释。", "只读监控；不发送生成请求。Ctrl+C退出，不停止模型。"])
    return "\n".join(rows)
