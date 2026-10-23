<p align="center">
  <img src="docs/assets/logo.svg" width="128" height="128" alt="ai-register Logo" />
</p>

<h1 align="center">ai-register</h1>

<p align="center">OpenAI / Grok / GonkaRouter 批量注册 · 邮箱验证码 · Token 保存与上传</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+" />
  <img src="https://img.shields.io/badge/Config-YAML-0F766E" alt="YAML 配置" />
  <img src="https://img.shields.io/badge/Mail_Providers-3-6366F1" alt="3 个邮箱 Provider" />
</p>

<p align="center"><strong>简体中文</strong> · <a href="README_en.md">English</a></p>

<p align="center">
  <a href="#本地运行">本地运行</a> · <a href="#docker-部署">Docker 部署</a> ·
  <a href="#邮箱服务">邮箱服务</a> · <a href="#配置">配置</a>
</p>

## 本地运行

需要 Python 3.9+ 和 uv。Grok、GonkaRouter 还需要 Chrome / Chromium；无桌面的 Linux 环境需要 Xvfb。

```bash
git clone https://github.com/ThinkerWen/ai-register.git
cd ai-register
uv sync
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，填写邮箱凭据、选择注册流程和代理，再启动：

```bash
uv run python main.py
```

<details>
<summary>使用 pip</summary>

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows 使用 `.venv\Scripts\activate` 激活环境；配置后运行 `python main.py`。

</details>

## Docker 部署

镜像已包含 Python、Chromium、Xvfb 和项目依赖。首次部署：

```bash
mkdir -p ai-register && cd ai-register
curl -fsSL https://raw.githubusercontent.com/ThinkerWen/ai-register/main/compose.yaml -o compose.yaml
curl -fsSL https://raw.githubusercontent.com/ThinkerWen/ai-register/main/config.example.yaml -o config.yaml
mkdir -p token_dir
```

编辑 `config.yaml` 后，拉取镜像、启动容器并执行任务：

```bash
docker pull designerwang/ai-register:latest
docker compose up -d --wait
docker exec -it ai-register python main.py
```

容器持续待命，每次 `docker exec` 执行一批任务，结果保存到宿主机 `./token_dir`。容器内 `TOKEN_DIR=/data/tokens` 会覆盖配置中的输出目录。独立版 Compose 使用 `docker-compose` 命令。

访问宿主机服务时使用 `host.docker.internal`，例如 `http://host.docker.internal:7890`；Linux 宿主机服务需监听容器可访问的地址。升级时保留配置，任务结束后重新执行 `pull` 和 `compose up`；停止用 `docker compose down`。

<details>
<summary>直接 docker run / 后台执行</summary>

准备好上述配置和目录后，也可直接启动容器：

```bash
docker run -d --name ai-register --restart unless-stopped --shm-size=1g \
  --add-host=host.docker.internal:host-gateway \
  --mount type=bind,src="$(pwd)/config.yaml",dst=/app/config.yaml,readonly \
  --mount type=bind,src="$(pwd)/token_dir",dst=/data/tokens \
  designerwang/ai-register:latest
```

后台运行并保存日志：

```bash
docker exec -d ai-register sh -c 'python main.py >> /data/tokens/register.log 2>&1'
tail -f token_dir/register.log
```

</details>

## 邮箱服务

| 配置值 | 服务 | 链接 |
| --- | --- | --- |
| `duckmail` | DuckMail | [www.duckmail.sbs](https://www.duckmail.sbs/) |
| `tempmail` | TempMail.lol | [tempmail.lol](https://tempmail.lol/) |
| `tempmail_hub` | Temp Mail Hub | [github.com/ThinkerWen/temp-mail](https://github.com/ThinkerWen/temp-mail) |

## 配置

完整参数与凭据格式见 [config.example.yaml](config.example.yaml)。

| 配置项 | 用途 |
| --- | --- |
| `model_provider` | `openai` / `grok` / `gonkarouter` |
| `mail_provider` | 选择上表中的邮箱服务 |
| `total_accounts` / `concurrency` | 任务总数 / 并发数 |
| `token_dir` | 本地输出目录，支持 `~` |
| `model_providers.openai.*` | OpenAI OAuth 和代理 |
| `mail_providers.<名称>.*` | 邮箱凭据和代理 |
| `cpa.*` / `g2a.*` | 可选上传到 [CPA](https://github.com/router-for-me/CLIProxyAPI) / [Grok2API](https://github.com/chenyme/grok2api) |
| `magpie.*` | 可选上传 GonkaRouter Key 到 Magpie |

GonkaRouter 的 API Key 追加到 `token_dir/gonkarouter/api_keys.txt`，一行一个。Turnstile 在直连环境可能无法生成 token，请通过 `model_providers.gonkarouter.proxy` 配置可用代理。

设置 `magpie.enable: true`，并填写 `magpie.api_url`、`magpie.magpie_auth_token`、`magpie.id` 和 `magpie.name` 后，Key 保存成功会同步上传到 Magpie；上传失败不会影响本地保存。`magpie_auth_token` 只填 `magpie_web_3430` 的 Cookie 值即可。
