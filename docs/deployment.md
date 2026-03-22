# Deployment Guide

## Local First

The default deployment is a single-machine daemon:

1. install Python dependencies with `uv`
2. configure `~/.assistant/config.toml`
3. place secrets in `.env`
4. run `uv run assistant start`

## Ubuntu VPS

### 1. Install system packages

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv build-essential nginx
```

### 2. Clone and install

```bash
git clone <your-repo>
cd SonarBot
uv sync --extra dev --extra memory
```

### 3. Configure

- create `~/.assistant/config.toml`
- create repo-local `.env`
- confirm `workspace/` is populated

### 4. systemd

Run:

```bash
uv run assistant onboard
```

If autostart is enabled, the wizard writes `~/.config/systemd/user/assistant.service`.

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now assistant.service
```

## WebChat Behind Nginx

Reverse proxy the Next.js frontend and the gateway separately.

Example idea:

- `localhost:3000` -> Next.js
- `localhost:8765` -> SonarBot gateway

Nginx should proxy:

- `/` to the webchat frontend
- `/api/`, `/webchat/`, `/ws`, and `/__health` to the gateway where appropriate

## TLS / Domain

Use Certbot with Nginx:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example
```

## Notes

- use Tailscale or an SSH tunnel for private remote access
- enable Docker only if you intend to use sandboxed exec
- if vector memory is enabled, install the `memory` extra
