# Configuration reference

Configuration is JSON with `"version": 1`. Default location:

- `$LLM_CONFIG`, when set;
- otherwise `$XDG_CONFIG_HOME/llm-manager/config.json`;
- otherwise `~/.config/llm-manager/config.json`.

`llm --config FILE ...` takes precedence. `llm init` writes the bundled example using exclusive create and never overwrites an existing configuration.

## Model registry

`models` is an array. Each `(name, engine)` pair must be unique.

```json
{
  "version": 1,
  "default_monitor": ["my-model", "sglang"],
  "variables": {"models_root": "${home}/models"},
  "models": [{
    "name": "my-model",
    "engine": "sglang",
    "variables": {"model_dir": "${models_root}/my-model"},
    "commands": {
      "start": ["my-launcher", "start"],
      "stop": ["my-launcher", "stop"],
      "status": ["my-launcher", "status"],
      "codex": ["codex", "-p", "my-model"]
    },
    "api": {"base_url": "http://127.0.0.1:30000/v1", "api_key_env": "LOCAL_MODEL_KEY"},
    "paths": {"weights": "${model_dir}", "configuration": "${model_dir}/config.json"},
    "logs": {"type": "file", "path": "${home}/.local/state/my-model.log"},
    "monitor": {"type": "sglang-log", "path": "${home}/.local/state/my-model.log", "stale_after": 15}
  }]
}
```

Omit `api_key_env` when authentication is not needed. The environment variable name is configured, not the credential value. API probes bypass HTTP proxy environment settings because this tool targets direct local/LAN inference endpoints.

## Command definitions

Each command is either an argv array or an object:

```json
{
  "argv": ["python3", "serve.py", "--model", "${model_dir}"],
  "cwd": "${home}/my-engine",
  "env": {"HF_HUB_OFFLINE": "1"}
}
```

The executable must be installed separately. Arguments are passed without `shell=True`; wildcard expansion, pipelines and command substitution are not performed. Explicitly configuring a shell executable is possible, but is then your chosen command rather than implicit behavior. Extra arguments after the action/model/engine are passed unchanged. `--dry-run` prints the planned argv, redacting common credential flags.

Variables use `${name}` syntax. Built-ins: `${home}`, `${config_dir}`, `${model}`, `${engine}`. Global `variables`, then target `variables`, override earlier values. Nested variables are supported, cycles or undefined variables are errors. Other dollar forms such as `$(...)` remain literal argv text; they do not execute.

Only trusted users should edit executable command configuration. `llm config` masks common credential keys and flags, but is not a general-purpose secret scanner. Keep credentials in environment variables or the engine's own credential store.

## Logs and named services

File source:

```json
{"type": "file", "path": "${home}/server.log"}
```

User journal source:

```json
{"type": "journal", "unit": "open-webui.service"}
```

`services` uses the same `commands`, `paths` and `logs` fields, but requires only a unique `name`, with optional `aliases`. Query it with `llm logs SERVICE`, `llm config SERVICE` or `llm status SERVICE`.

For launchers with multiple context configurations, add `"contexts": ["32k", "256k"]`, `"variables": {"context": "256k"}`, and use `${context}` in paths. `llm logs MODEL ENGINE --context 32k` and `llm config MODEL ENGINE --context 32k` override that variable. Optional `logs.host_path` enables `--host`.

## Downloads

`downloads` is an array with a unique `name` and a required local `file` path. Optional fields:

| Field | Meaning |
| --- | --- |
| `model`, `engine` | Allow selection with `llm download MODEL ENGINE` |
| `rpc_url` | aria2 JSON-RPC endpoint |
| `rpc_secret_env` | Name of environment variable holding aria2 RPC secret |
| `total_bytes` | Expected file length from the selected model revision |
| `verified_marker` | Path written by your external checksum verifier on success |

The monitor matches aria2 entries by the exact expanded file path. It does not launch or control downloads. RPC errors fall back to explicitly labeled disk allocation estimates; they do not establish file integrity. CLI JSON output is an array because one configuration can contain several downloads. Continuous JSON output is one array per line.

## Web links

```json
{"web": [{"name": "Chat", "url": "http://127.0.0.1:3000/"}]}
```

`llm web` checks the HTTP page; `--no-check` only lists links. Page accessibility does not validate inference.
