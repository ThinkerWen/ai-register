# ai-register

[中文](README.md) | English

A lightweight batch registration tool with OpenAI and Grok flows, temporary mailbox OTP retrieval, and optional CPA/Grok2API upload.

## Features

- Concurrent batch execution
- Switchable mail provider
- OpenAI OAuth and Grok provider switching
- [CPA](https://github.com/router-for-me/CLIProxyAPI) upload support
- [grok2api](https://github.com/chenyme/grok2api) upload support

## Quick Start

### 1) Install dependencies

Option A (recommended, uv):

```bash
uv sync
```

Option B (pip):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2) Initialize config

```bash
cp config.example.yaml config.yaml
```

Then fill in sensitive fields:

- `mail_providers.duckmail.bearer`
- `mail_providers.tempmail.api_key`
- `cpa.token` (only required when CPA upload is enabled)
- `g2a.token` (only required when Grok2API upload is enabled)

### 3) Run

The unified entry point is `main.py`.

Check configuration first:

```bash
python main.py
```

Run batch flow:

```bash
python main.py
```

The actual flow is selected by `model_provider` in `config.yaml`.

## Configuration Reference

| Field | Description                             |
| --- |-----------------------------------------|
| `concurrency` | Number of concurrent workers            |
| `total_accounts` | Total number of target accounts         |
| `proxy` | Global proxy; leave empty to disable    |
| `token_dir` | Token output directory                  |
| `model_provider` | Model provider name (`openai` / `grok`) |
| `model_providers.openai.*` | OpenAI OAuth configuration              |
| `model_providers.grok.browser_proxy` | Grok browser proxy setting              |
| `mail_provider` | Mail provider (`duckmail` / `tempmail`) |
| `mail_providers.duckmail.*` | DuckMail settings                       |
| `mail_providers.tempmail.*` | TempMail settings                       |
| `cpa.enable` | Enable CPA upload                       |
| `cpa.api_url` | CPA upload endpoint                     |
| `cpa.token` | CPA login token                         |
| `g2a.enable` | Enable Grok2API upload                  |
| `g2a.api_url` | Grok2API upload endpoint                |
| `g2a.token` | Grok2API login token                    |

See [config.example.yaml](config.example.yaml) for a complete example.

## Environment Variable Overrides

Supports overriding part of the config via environment variables. Common ones include:

- `CONCURRENCY`
- `TOTAL_ACCOUNTS`
- `PROXY`
- `MODEL_PROVIDER`
- `MAIL_PROVIDER`
- `TOKEN_DIR`
- `CPA_ENABLE`
- `CPA_API_URL`
- `CPA_TOKEN`
