<p align="center">
  <img src="docs/assets/logo.svg" width="128" height="128" alt="ai-register logo" />
</p>

<h1 align="center">ai-register</h1>

<p align="center"><strong>One configuration for registration flows and temporary mail.</strong></p>

<p align="center">
  Batch registration for OpenAI and Grok, with email verification, local token storage, and optional uploads.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+" />
  <img src="https://img.shields.io/badge/Config-YAML-0F766E" alt="YAML configuration" />
  <img src="https://img.shields.io/badge/Mail_Providers-3-6366F1" alt="3 mail providers" />
</p>

<p align="center"><a href="README.md">简体中文</a> · <strong>English</strong></p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#mail-providers">Mail providers</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#development--contributing">Contributing</a>
</p>

---

## Features

| Capability | Details |
| --- | --- |
| Two registration flows | Select OpenAI or Grok with `model_provider`, including OpenAI OAuth support |
| Three mail providers | DuckMail, TempMail.lol, and Temp Mail Hub |
| Email verification | Poll inboxes and extract verification codes from message subjects and bodies |
| Concurrent batches | Configure batch size and concurrency independently, with runtime logs |
| Storage and uploads | Save tokens locally and optionally upload to CPA or Grok2API |

## Quick start

### 1. Clone and install

Use Python 3.9+; `uv` is the recommended dependency manager. The Grok flow also requires Chrome / Chromium. Linux environments without a graphical display need Xvfb.

```bash
git clone https://github.com/ThinkerWen/ai-register.git
cd ai-register
uv sync
```

<details>
<summary>Install with pip instead</summary>

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows, activate the environment with `.venv\Scripts\activate`.

</details>

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` to select a registration flow and mail provider, then enter credentials for the chosen service. These are the main top-level settings; see [config.example.yaml](config.example.yaml) for the full configuration.

```yaml
model_provider: "openai"             # openai / grok
mail_provider: "tempmail"           # duckmail / tempmail / tempmail_hub
total_accounts: 1                   # Tasks in this batch
concurrency: 1                      # Concurrent workers
proxy: "http://127.0.0.1:7890"       # Adjust for your network
token_dir: "~/.ai-register/token_dir"
```

Set mail credentials under `mail_providers.<name>` as described in [Mail providers](#mail-providers). CPA and Grok2API uploads are disabled by default; configure them when enabling uploads.

### 3. Run

From the project root:

```bash
uv run python main.py
```

The program logs its configuration and starts the batch. If installed with pip, run `python main.py` in the activated virtual environment.

## Mail providers

Choose a service with the top-level `mail_provider` setting. All three share the same verification-code interface.

| Value | Service | Link | Settings under `mail_providers.<name>` |
| --- | --- | --- | --- |
| `duckmail` | DuckMail | [www.duckmail.sbs](https://www.duckmail.sbs/) | `api_base`, `bearer` |
| `tempmail` | TempMail.lol | [tempmail.lol](https://tempmail.lol/) | `api_key`, plus optional `domain`, `prefix`, and `proxy` |
| `tempmail_hub` | Temp Mail Hub | [github.com/ThinkerWen/temp-mail](https://github.com/ThinkerWen/temp-mail) | `api_base`, `api_token`, upstream selection, and polling options |

See [config.example.yaml](config.example.yaml) for the complete settings for each service.

## Configuration

### Registration and output

| Setting | Description |
| --- | --- |
| `model_provider` | Registration flow: `openai` / `grok` |
| `model_providers.openai.*` | OAuth flags, issuer, client ID, and redirect URI |
| `model_providers.grok.browser_proxy` | Grok browser proxy; falls back to the global proxy when unset |
| `total_accounts` | Tasks in the batch |
| `concurrency` | Concurrent workers |
| `token_dir` | Token output directory; supports `~`; an absolute path is recommended |

With the output directory from the example configuration, files are organized as follows:

```text
~/.ai-register/token_dir/
├── openai/
│   └── <email>.json          # OAuth tokens
└── grok/
    └── sso_<timestamp>.txt   # SSO tokens
```

### Optional uploads

| Destination | Enable with | Connection settings |
| --- | --- | --- |
| [CPA / CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | `cpa.enable` | `cpa.api_url`, `cpa.token` |
| [Grok2API](https://github.com/chenyme/grok2api) | `g2a.enable` | `g2a.api_url`, `g2a.admin_username`, `g2a.admin_password` |

OpenAI tokens can be uploaded to CPA, and Grok SSO tokens to Grok2API. Set `g2a.api_url` to the service base URL; the program signs in to the management API with the administrator credentials.

### Proxies

| Scope | Behavior |
| --- | --- |
| Global `proxy` | Used by registration flows; leave empty to avoid explicitly setting a global proxy |
| `mail_providers.tempmail.proxy` | Takes priority over the global proxy; falls back to it when empty |
| `mail_providers.tempmail_hub.proxy` | Direct by default; does not inherit global or environment proxies |
| `cpa.use_proxy` / `g2a.use_proxy` | Set to `true` to use the global proxy for the corresponding uploads; defaults to `false` |

### Environment variables

Override these settings for an individual run:

```bash
MODEL_PROVIDER=grok TOTAL_ACCOUNTS=5 CONCURRENCY=2 uv run python main.py
```

<details>
<summary>Supported environment variables</summary>

| Group | Variables |
| --- | --- |
| Tasks | `MODEL_PROVIDER`, `MAIL_PROVIDER`, `TOTAL_ACCOUNTS`, `CONCURRENCY` |
| Network and output | `PROXY`, `TOKEN_DIR` |
| OpenAI OAuth | `MODEL_ENABLE_OAUTH`, `MODEL_OAUTH_REQUIRED`, `MODEL_OAUTH_ISSUER`, `MODEL_OAUTH_CLIENT_ID`, `MODEL_OAUTH_REDIRECT_URI` |
| CPA | `CPA_ENABLE`, `CPA_API_URL`, `CPA_TOKEN`, `CPA_USE_PROXY` |
| Grok2API | `G2A_ENABLE`, `G2A_API_URL`, `G2A_ADMIN_USERNAME`, `G2A_ADMIN_PASSWORD`, `G2A_USE_PROXY` |

Configure mail service credentials under `mail_providers` in `config.yaml`.

</details>

## Development & contributing

```text
ai-register/
├── main.py                 # Unified entry point
├── config.example.yaml     # Configuration template
├── register/               # OpenAI / Grok registration flows
├── util/
│   ├── providers/          # Mail adapters
│   ├── mail.py             # Provider selection and code retrieval
│   ├── config.py           # Configuration and environment overrides
│   ├── cpa.py              # CPA uploads
│   └── g2a.py              # Grok2API uploads
└── tests/                  # Automated tests
```

Run the existing tests:

```bash
uv run python -m unittest discover -s tests -v
```

[Issues](https://github.com/ThinkerWen/ai-register/issues) and pull requests are welcome. Include reproduction steps, your runtime environment, and redacted logs when reporting a problem. To add a mail adapter, start with the [MailProvider interface](util/providers/base.py) and [Temp Mail Hub implementation](util/providers/tempmail_hub.py).
