# llm-manager

A lightweight, configuration-driven CLI for local model servers. Python 3.10+, no runtime dependencies.

Manage model start/stop/status commands, logs, SGLang throughput, GPU metrics, existing aria2 download progress, configuration paths, and web links from one command.

```bash
uv tool install git+https://github.com/zzsoszz/llm-manager.git
llm init
# Edit ~/.config/llm-manager/config.json for your machine.
llm list
llm qwen38 sglang start
llm logs qwen38 sglang -f
llm monitor qwen38 sglang
llm download -f
llm config qwen38 sglang
llm web
```

Use `llm -c /path/to/config.json ...` or `LLM_CONFIG` for another configuration. Both `llm start MODEL ENGINE` and `llm MODEL ENGINE start` work. `llm speed` remains an alias for `monitor`.

The generated configuration is an example, not an installed model server. Install and configure your inference engine separately. The example uses Linux user-level systemd; model commands can be replaced with existing launchers on other systems. Commands are argv arrays, executed without a shell.

SGLang monitoring reads its batch logs and probes `/v1/models`; it never submits generation requests. Rates are aggregate batch throughput including reasoning tokens, not per-response decode speed. Stale and offline samples are not shown as current; queue counts are historical samples. AMD telemetry uses Linux sysfs; NVIDIA telemetry uses an existing `nvidia-smi`. Use `--no-gpu` to avoid hardware polling. File logs require `tail`; journal logs require `journalctl --user`.

The `download` command observes downloads registered in configuration. It does not start downloads, verify hashes, or install models. Disk fallback is explicitly an estimate; a verification marker is only as trustworthy as the downloader creating it. See the [configuration reference](docs/configuration.md) and [Chinese README](README.md) for details.

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```

Licensed under MIT. Contributions and additional monitoring adapters are welcome.
