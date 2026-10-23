<p align="center">
  <img src="docs/assets/logo.svg" width="128" height="128" alt="ai-register logo" />
</p>

<h1 align="center">ai-register</h1>

<p align="center">OpenAI / Grok / GonkaRouter batch registration · Email verification · Token storage and uploads</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+" />
  <img src="https://img.shields.io/badge/Config-YAML-0F766E" alt="YAML configuration" />
  <img src="https://img.shields.io/badge/Mail_Providers-3-6366F1" alt="3 mail providers" />
</p>

<p align="center"><a href="README.md">简体中文</a> · <strong>English</strong></p>

<p align="center">
  <a href="#local-setup">Local setup</a> · <a href="#docker">Docker</a> ·
  <a href="#mail-providers">Mail providers</a> · <a href="#configuration">Configuration</a>
</p>

## Local setup

Requires Python 3.9+ and uv. Grok and GonkaRouter also need Chrome / Chromium, plus Xvfb on Linux without a desktop.

```bash
git clone https://github.com/ThinkerWen/ai-register.git
cd ai-register
uv sync
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your mail credentials, registration flow, and proxies, then run:

```bash
uv run python main.py
```

<details>
<summary>Using pip</summary>

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows, activate with `.venv\Scripts\activate`. After configuring, run `python main.py`.

</details>

## Docker

The image includes Python, Chromium, Xvfb, and project dependencies. For a new deployment:

```bash
mkdir -p ai-register && cd ai-register
curl -fsSL https://raw.githubusercontent.com/ThinkerWen/ai-register/main/compose.yaml -o compose.yaml
curl -fsSL https://raw.githubusercontent.com/ThinkerWen/ai-register/main/config.example.yaml -o config.yaml
mkdir -p token_dir
```

Edit `config.yaml`, then pull the image, start the container, and run a batch:

```bash
docker pull designerwang/ai-register:latest
docker compose up -d --wait
docker exec -it ai-register python main.py
```

The container stays idle between batches. Each `docker exec` starts a run, with output saved to `./token_dir` on the host. Container variable `TOKEN_DIR=/data/tokens` overrides the configured output path. Use `docker-compose` if you have standalone Compose installed.

Use `host.docker.internal` to reach host services, for example `http://host.docker.internal:7890`; on Linux, the host service must listen on an address reachable from the container. To upgrade, keep your configuration and repeat `pull` and `compose up` after the current task ends. Stop with `docker compose down`.

<details>
<summary>Direct docker run / background execution</summary>

With the configuration and directories prepared above, you can also start the container directly:

```bash
docker run -d --name ai-register --restart unless-stopped --shm-size=1g \
  --add-host=host.docker.internal:host-gateway \
  --mount type=bind,src="$(pwd)/config.yaml",dst=/app/config.yaml,readonly \
  --mount type=bind,src="$(pwd)/token_dir",dst=/data/tokens \
  designerwang/ai-register:latest
```

Run in the background and save logs:

```bash
docker exec -d ai-register sh -c 'python main.py >> /data/tokens/register.log 2>&1'
tail -f token_dir/register.log
```

</details>

## Mail providers

| Value | Service | Link |
| --- | --- | --- |
| `duckmail` | DuckMail | [www.duckmail.sbs](https://www.duckmail.sbs/) |
| `tempmail` | TempMail.lol | [tempmail.lol](https://tempmail.lol/) |
| `tempmail_hub` | Temp Mail Hub | [github.com/ThinkerWen/temp-mail](https://github.com/ThinkerWen/temp-mail) |

## Configuration

See [config.example.yaml](config.example.yaml) for all settings and credential formats.

| Setting | Purpose |
| --- | --- |
| `model_provider` | `openai` / `grok` / `gonkarouter` |
| `mail_provider` | Select a mail provider from the table above |
| `total_accounts` / `concurrency` | Batch size / concurrent workers |
| `token_dir` | Local output directory; supports `~` |
| `model_providers.openai.*` | OpenAI OAuth and proxy settings |
| `mail_providers.<name>.*` | Mail credentials and proxies |
| `cpa.*` / `g2a.*` | Optional uploads to [CPA](https://github.com/router-for-me/CLIProxyAPI) / [Grok2API](https://github.com/chenyme/grok2api) |
| `magpie.*` | Optional GonkaRouter key uploads to Magpie |

GonkaRouter API keys are appended to `token_dir/gonkarouter/api_keys.txt`, one per line. Turnstile may not issue a token over a direct connection; configure a working proxy with `model_providers.gonkarouter.proxy`.

When `magpie.enable` is `true` and `magpie.api_url`, `magpie.magpie_auth_token`, `magpie.id`, and `magpie.name` are set, each saved key is also uploaded to Magpie. Upload failures do not affect local storage. Set `magpie_auth_token` to the `magpie_web_3430` cookie value only.
