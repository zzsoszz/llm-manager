# llm-manager

用一个 `llm` 命令管理本地模型服务器：启动、停止、状态、日志、实时速度、下载进度，以及程序和配置路径。

[English](README.en.md) · [配置说明](docs/configuration.md) · [MIT License](LICENSE)

Python 3.10+，**零运行时依赖**。模型、引擎、路径和服务名称由自己的 JSON 配置定义；源码不包含作者机器的配置。SGLang、llama.cpp、Strata 或其他引擎的启动器都可以注册为命令。

## 安装

```bash
uv tool install git+https://github.com/zzsoszz/llm-manager.git

# 或使用隔离的 Python 环境
python3 -m venv .venv
.venv/bin/pip install git+https://github.com/zzsoszz/llm-manager.git
.venv/bin/llm --version
```

需要本机已有 `uv`，或使用系统的 Python/venv。也可从源码安装：

```bash
git clone https://github.com/zzsoszz/llm-manager.git
cd llm-manager
python3 -m pip install .
```

## 开始使用

```bash
llm init                         # 创建示例配置，不覆盖已有文件
llm config                       # 查看配置路径
```

编辑 `~/.config/llm-manager/config.json`，填入自己的模型目录、启动命令、API 地址、日志和网页地址，再运行：

```bash
llm list
llm qwen38 sglang start
llm qwen38 sglang status
llm qwen38 sglang stop
```

`llm init` **不会安装或启动模型引擎**。示例使用 Linux 用户级 systemd 服务；先根据自己的设备修改 [示例服务](examples/sglang-qwen.service)，或将配置中的命令换成现有启动器。自定义命令也可用于非 Linux 系统。

## 命令

| 命令 | 用途 |
| --- | --- |
| `llm list` | 已登记模型及动作 |
| `llm qwen38 sglang start` | 启动模型 |
| `llm qwen38 sglang stop` | 停止模型 |
| `llm qwen38 sglang status` | 调用该模型的状态命令 |
| `llm status` | 查看已配置 API 的可访问状态 |
| `llm logs qwen38 sglang -n 100` | 最近日志 |
| `llm logs qwen38 sglang -f` | 实时日志 |
| `llm logs webui -f` | 网页服务日志 |
| `llm monitor qwen38 sglang` | 持续监控速度及显卡 |
| `llm monitor qwen38 sglang --once --json` | 单次机器可读采样 |
| `llm monitor qwen38 sglang --no-gpu` | 仅监控引擎，不轮询显卡 |
| `llm download` | 已登记权重下载任务的进度 |
| `llm download -f` | 持续刷新下载进度 |
| `llm config qwen38 sglang` | 程序、模型、日志和配置路径 |
| `llm config webui` | 网页服务配置 |
| `llm web` | 网页地址及可访问状态 |
| `llm web --no-check` | 只列出地址 |

动作也可以放在前面：`llm start qwen38 sglang`。`llm speed` 是 `llm monitor` 的兼容别名。

原有 `llm qwen38 sglang log` 形式默认实时跟随；统一的 `llm logs qwen38 sglang` 默认显示最近100行。`Ctrl+C` 退出日志、监控或进度查看，不会停止模型或下载。启动命令自身以前台方式运行时，其退出行为由启动器决定。

```bash
llm qwen38 sglang start --dry-run   # 只显示参数，不启动
llm -c /path/to/config.json list    # 使用另一份配置
LLM_CONFIG=/path/to/config.json llm list
```

可以在配置中注册 `codex` 等额外动作，例如 `llm qwen38 sglang codex`，程序只执行你提供的启动命令，不生成 Codex 配置。

## 监控口径与支持范围

- **实时速度目前只实现 SGLang 日志适配器**，读取 `Decode batch` / `Prefill batch` 的吞吐记录。它不是 SGLang 官方 CLI，不依赖 Prometheus/Grafana。
- 显示的是**批次总吞吐**，包含思考 token；不同于“单次回答输出 token / 总耗时”。不会把流式消息块数冒充 token 数。
- 请求数来自最近日志采样，不是精确的即时队列。日志过期或 API 离线时，当前速度显示为空，历史记录单独保留。长 Prefill 期间也可能暂时没有新日志，不能据此判定空闲。
- 只读取模型列表 `/v1/models` 探测 API；不会调用生成或 abort 接口。API 在线不代表完整生成能力已经通过测试。
- 日志时间按运行 CLI 的本机时区解释。远程日志应先统一时区。
- AMD 指标来自 Linux `sysfs`；NVIDIA 指标来自已有的 `nvidia-smi`。缺少权限或工具时对应指标省略，不要求安装 GPU SDK。轮询 GPU 可能影响其空闲状态，可使用 `--no-gpu`。
- 启停支持任意已配置引擎；**不承诺其他引擎的实时速度适配**。文件日志依赖 `tail`，系统日志依赖 `journalctl --user`。

## 下载进度

`download` 只查询已有任务，既不是下载器，也不会下载模型。将下载器的 aria2 RPC 地址、目标文件、总字节数填入配置的 `downloads`：

```json
{
  "name": "qwen38-weights",
  "model": "qwen38",
  "engine": "sglang",
  "rpc_url": "http://127.0.0.1:6800/jsonrpc",
  "rpc_secret_env": "ARIA2_RPC_SECRET",
  "file": "${home}/models/Qwen3.8-27B-AWQ/model.safetensors",
  "total_bytes": 18698408384,
  "verified_marker": "${home}/models/Qwen3.8-27B-AWQ/verified.json"
}
```

总大小必须使用**所选权重版本实际公布的大小**。设置了 `rpc_secret_env` 时，在启动 CLI 的环境中提供对应变量。

RPC 不可用时会标明“磁盘估算”；预分配、稀疏文件和压缩可能使它偏离真实下载进度。`verified_marker` 只是你的校验程序在完成哈希校验后写入的成功标记；本 CLI **不会自行校验权重**。下载100%不等于模型服务已就绪。

## 开发

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```

测试覆盖配置校验、参数原样传递、兼容命令顺序、日志解析、过期/离线状态、GPU 采样、RPC 下载匹配和初始化保护，不需要模型权重或 GPU。欢迎通过 Issues 报告问题，通过 PR 增加新的引擎监控适配器。
