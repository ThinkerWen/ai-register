# ai-register

中文 | [English](README_en.md)

轻量的批量注册脚本工具，支持 OpenAI 与 Grok 两套注册流程，以及临时邮箱验证码读取和可选的 CPA/Grok2API 上传。

## 功能特性

- 并发批量执行
- 可切换邮箱 provider
- 支持 OpenAI OAuth 与 Grok provider 切换
- 支持 [CPA](https://github.com/router-for-me/CLIProxyAPI) 上传
- 支持 [grok2api](https://github.com/chenyme/grok2api) 上传

## 快速开始

### 1) 安装依赖

方式 A（推荐，uv）:

```bash
uv sync
```

方式 B（pip）:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2) 初始化配置

```bash
cp config.example.yaml config.yaml
```

然后填写敏感项:

- `mail_providers.duckmail.bearer`
- `mail_providers.tempmail.api_key`
- `cpa.token`（仅在启用 CPA 上传时需要）

### 3) 启动

入口统一使用 `main.py`。

先做配置检查:

```bash
python main.py
```

执行批量流程:

```bash
python main.py
```

通过 `config.yaml` 里的 `model_provider` 选择执行 `openai` 或 `grok`。

## 配置说明

| 字段 | 说明                                   |
| --- |--------------------------------------|
| `concurrency` | 并发数                                  |
| `total_accounts` | 目标注册账号总数                             |
| `proxy` | 全局代理，留空表示不使用                         |
| `token_dir` | token 输出目录                           |
| `model_provider` | 模型 provider 名称（`openai` / `grok`）    |
| `model_providers.openai.*` | OpenAI OAuth 配置                      |
| `model_providers.grok.browser_proxy` | Grok 浏览器代理配置                         |
| `mail_provider` | 邮箱 provider（`duckmail` / `tempmail`） |
| `mail_providers.duckmail.*` | DuckMail 配置                          |
| `mail_providers.tempmail.*` | TempMail 配置                          |
| `cpa.enable` | 是否启用 CPA 上传                          |
| `cpa.api_url` | CPA 上传接口地址                           |
| `cpa.token` | CPA 登录 token                         |
| `g2a.enable` | 是否启用 Grok2API 上传                     |
| `g2a.api_url` | Grok2API 上传接口地址                      |
| `g2a.token` | Grok2API 登录 token                    |
| `cpa.use_proxy` | 上传 CPA 时是否强制使用全局 `proxy`（默认 false；为 true 时使用 `proxy`，否则本地地址可能绕过代理） |
| `g2a.use_proxy` | 上传 Grok2API 时是否强制使用全局 `proxy`（默认 false；为 true 时使用 `proxy`，否则本地地址可能绕过代理） |

示例配置请参考 [config.example.yaml](config.example.yaml)。

## 环境变量覆盖

支持使用环境变量覆盖部分配置，常用项如下:

- `CONCURRENCY`
- `TOTAL_ACCOUNTS`
- `PROXY`
- `MODEL_PROVIDER`
- `MAIL_PROVIDER`
- `TOKEN_DIR`
- `CPA_ENABLE`
- `CPA_API_URL`
- `CPA_TOKEN`
 - `CPA_USE_PROXY`
 - `G2A_USE_PROXY`
