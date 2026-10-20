<p align="center">
  <img src="docs/assets/logo.svg" width="128" height="128" alt="ai-register Logo" />
</p>

<h1 align="center">ai-register</h1>

<p align="center"><strong>统一配置，连接注册流程与临时邮箱。</strong></p>

<p align="center">
  支持 OpenAI 与 Grok 的批量注册工具，集成邮箱验证码读取、Token 保存与可选上传。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+" />
  <img src="https://img.shields.io/badge/Config-YAML-0F766E" alt="YAML 配置" />
  <img src="https://img.shields.io/badge/Mail_Providers-3-6366F1" alt="3 个邮箱 Provider" />
</p>

<p align="center"><strong>简体中文</strong> · <a href="README_en.md">English</a></p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#邮箱服务">邮箱服务</a> ·
  <a href="#配置参考">配置参考</a> ·
  <a href="#开发与贡献">开发与贡献</a>
</p>

---

## 功能概览

| 能力 | 说明 |
| --- | --- |
| 两套注册流程 | 通过 `model_provider` 切换 OpenAI 或 Grok，支持 OpenAI OAuth |
| 三种邮箱接入 | DuckMail、TempMail.lol、Temp Mail Hub |
| 自动读取验证码 | 轮询收件箱，从邮件主题与正文中提取验证码 |
| 并发批量执行 | 独立配置任务总数与并发数，输出运行日志 |
| 保存与上传 | 本地保存 Token，可选上传至 CPA 或 Grok2API |

## 快速开始

### 1. 获取项目并安装依赖

准备 Python 3.9+，推荐使用 `uv` 管理依赖。Grok 流程还需要可用的 Chrome / Chromium；无图形界面的 Linux 环境需要 Xvfb。

```bash
git clone https://github.com/ThinkerWen/ai-register.git
cd ai-register
uv sync
```

<details>
<summary>使用 pip 安装</summary>

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows 下使用 `.venv\Scripts\activate` 激活环境。

</details>

### 2. 配置运行参数

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，选择注册流程和邮箱服务，并填写所选服务的凭据。下面是需要关注的顶层参数；完整配置见 [config.example.yaml](config.example.yaml)。

```yaml
model_provider: "openai"             # openai / grok
mail_provider: "tempmail"           # duckmail / tempmail / tempmail_hub
total_accounts: 1                   # 本批任务总数
concurrency: 1                      # 并发数
proxy: "http://127.0.0.1:7890"       # 按实际网络环境调整
token_dir: "~/.ai-register/token_dir"
```

邮箱凭据填写在 `mail_providers.<名称>` 下，具体说明见[邮箱服务](#邮箱服务)。CPA 与 Grok2API 上传默认关闭，启用后再填写对应配置。

### 3. 启动任务

在项目根目录执行：

```bash
uv run python main.py
```

程序会输出配置与运行日志，然后执行批量任务。使用 pip 安装时，在已激活的虚拟环境中运行 `python main.py`。

## 邮箱服务

通过顶层 `mail_provider` 选择服务，三种接入方式共用验证码读取接口。

| 配置值 | 服务 | 链接 | 配置项（位于 `mail_providers.<名称>` 下） |
| --- | --- | --- | --- |
| `duckmail` | DuckMail | [www.duckmail.sbs](https://www.duckmail.sbs/) | `api_base`、`bearer` |
| `tempmail` | TempMail.lol | [tempmail.lol](https://tempmail.lol/) | `api_key`，以及可选的 `domain`、`prefix`、`proxy` |
| `tempmail_hub` | Temp Mail Hub | [github.com/ThinkerWen/temp-mail](https://github.com/ThinkerWen/temp-mail) | `api_base`、`api_token`，以及上游选择与轮询参数 |

各服务的完整配置见 [config.example.yaml](config.example.yaml)。

## 配置参考

### 注册与输出

| 配置项 | 说明 |
| --- | --- |
| `model_provider` | 注册流程：`openai` / `grok` |
| `model_providers.openai.*` | OAuth 开关、Issuer、Client ID 与回调地址 |
| `model_providers.grok.browser_proxy` | Grok 浏览器代理，未设置时使用全局代理 |
| `total_accounts` | 本批任务总数 |
| `concurrency` | 并发任务数 |
| `token_dir` | Token 保存目录，支持 `~`；建议使用绝对路径 |

使用示例配置中的输出目录时，文件组织如下：

```text
~/.ai-register/token_dir/
├── openai/
│   └── <email>.json          # OAuth Token
└── grok/
    └── sso_<timestamp>.txt   # SSO Token
```

### 可选上传

| 目标 | 开关 | 连接配置 |
| --- | --- | --- |
| [CPA / CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | `cpa.enable` | `cpa.api_url`、`cpa.token` |
| [Grok2API](https://github.com/chenyme/grok2api) | `g2a.enable` | `g2a.api_url`、`g2a.admin_username`、`g2a.admin_password` |

OpenAI Token 可上传至 CPA，Grok SSO Token 可上传至 Grok2API。`g2a.api_url` 填写服务基础地址，程序使用管理员账号密码登录管理接口。

### 代理设置

| 范围 | 行为 |
| --- | --- |
| 全局 `proxy` | 用于注册流程；留空表示不显式设置全局代理 |
| `mail_providers.tempmail.proxy` | 优先于全局代理；留空时回退到全局代理 |
| `mail_providers.tempmail_hub.proxy` | 默认直连，不继承全局或环境代理 |
| `cpa.use_proxy` / `g2a.use_proxy` | 设为 `true` 时，将全局代理用于对应上传请求；默认 `false` |

### 环境变量

环境变量可覆盖以下配置，适合临时切换任务参数：

```bash
MODEL_PROVIDER=grok TOTAL_ACCOUNTS=5 CONCURRENCY=2 uv run python main.py
```

<details>
<summary>查看支持的环境变量</summary>

| 分组 | 环境变量 |
| --- | --- |
| 任务 | `MODEL_PROVIDER`、`MAIL_PROVIDER`、`TOTAL_ACCOUNTS`、`CONCURRENCY` |
| 网络与输出 | `PROXY`、`TOKEN_DIR` |
| OpenAI OAuth | `MODEL_ENABLE_OAUTH`、`MODEL_OAUTH_REQUIRED`、`MODEL_OAUTH_ISSUER`、`MODEL_OAUTH_CLIENT_ID`、`MODEL_OAUTH_REDIRECT_URI` |
| CPA | `CPA_ENABLE`、`CPA_API_URL`、`CPA_TOKEN`、`CPA_USE_PROXY` |
| Grok2API | `G2A_ENABLE`、`G2A_API_URL`、`G2A_ADMIN_USERNAME`、`G2A_ADMIN_PASSWORD`、`G2A_USE_PROXY` |

邮箱服务凭据通过 `config.yaml` 中的 `mail_providers` 配置。

</details>

## 开发与贡献

```text
ai-register/
├── main.py                 # 统一入口
├── config.example.yaml     # 配置模板
├── register/               # OpenAI / Grok 注册流程
├── util/
│   ├── providers/          # 邮箱适配器
│   ├── mail.py             # Provider 选择与验证码读取
│   ├── config.py           # 配置加载与环境变量覆盖
│   ├── cpa.py              # CPA 上传
│   └── g2a.py              # Grok2API 上传
└── tests/                  # 自动化测试
```

运行现有测试：

```bash
uv run python -m unittest discover -s tests -v
```

欢迎通过 [Issue](https://github.com/ThinkerWen/ai-register/issues) 报告问题或提出建议，也欢迎提交 Pull Request。问题描述请附上复现步骤、运行环境和脱敏日志；新增邮箱适配器可参考 [MailProvider 接口](util/providers/base.py) 与 [Temp Mail Hub 实现](util/providers/tempmail_hub.py)。
