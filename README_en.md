# ai-register

[中文](README.md) | English

A lightweight batch registration tool with OpenAI and Grok flows, temporary mailbox OTP retrieval, and optional CPA upload.

## Features

- Concurrent batch execution
- Switchable mail provider
- OpenAI OAuth and Grok provider switching
- CPA upload support

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

| Field | Description |
| --- | --- |
| `concurrency` | Number of concurrent workers |
| `total_accounts` | Total number of target accounts |
| `proxy` | Global proxy; leave empty to disable |
| `token_dir` | Token output directory |
| `model_provider` | Model provider name (`openai` / `grok`) |
| `model_providers.openai.*` | OpenAI OAuth configuration |
| `model_providers.grok.*` | Grok external flow bridge settings |
| `mail_provider` | Mail provider (`duckmail` / `tempmail`) |
| `mail_providers.duckmail.*` | DuckMail settings |
| `mail_providers.tempmail.*` | TempMail settings |
| `cpa.enable` | Enable CPA upload |
| `cpa.api_url` | CPA upload endpoint |
| `cpa.token` | CPA login token |

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

## Security Notes

- Do not commit real `config.yaml` containing secrets.
- Commit only sanitized example files such as [config.example.yaml](config.example.yaml).
- If CPA endpoint points to local addresses (`localhost`, `127.0.0.1`), proxy is bypassed automatically.

## Grok Notes

- With `model_provider: grok`, the project bridges to the external Grok registration flow referenced by `model_providers.grok.source_path`.
- The Grok flow currently supports only `mail_provider: duckmail`.
- Grok `sso` output is written to `token_dir/grok/`.
