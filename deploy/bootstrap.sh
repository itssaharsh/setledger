#!/usr/bin/env bash
# Runs ON the droplet as root, from the unpacked release in /opt/setledger.
# Idempotent: safe to re-run on every deploy.
set -euo pipefail
APP=/opt/setledger
DOMAIN="${1:-}"

export DEBIAN_FRONTEND=noninteractive
if ! command -v fpcalc >/dev/null || ! command -v caddy >/dev/null; then
  apt-get update -q
  apt-get install -y -q ffmpeg libchromaprint-tools python3-venv debian-keyring \
    debian-archive-keyring apt-transport-https curl gnupg
  if ! command -v caddy >/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -q && apt-get install -y -q caddy
  fi
fi

# 2 GB swap: torch import spikes past RAM briefly on a 2 GB box
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

id setledger >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin setledger
chown -R setledger:setledger "$APP"

as_app() { sudo -u setledger HOME="$APP" "$@"; }
[ -x "$APP/.venv/bin/python" ] || as_app python3 -m venv "$APP/.venv"
as_app "$APP/.venv/bin/pip" install -q --upgrade pip
as_app "$APP/.venv/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu
as_app "$APP/.venv/bin/pip" install -q -e "$APP[clap]"

# deterministic demo world (byte-identical to the build machine; the committed CLAP cache
# is keyed to these exact files)
[ -f "$APP/data/mixes/demo_set.wav" ] || (cd "$APP" && as_app .venv/bin/python scripts/fetch_corpus.py)
# pre-fetch CLAP weights so the first page load doesn't wait on a 600 MB download
as_app "$APP/.venv/bin/python" -c "from transformers import ClapModel, ClapProcessor as P; \
P.from_pretrained('laion/clap-htsat-unfused'); ClapModel.from_pretrained('laion/clap-htsat-unfused')"

install -m 644 "$APP/deploy/setledger.service" /etc/systemd/system/setledger.service
install -m 644 "$APP/deploy/Caddyfile" /etc/caddy/Caddyfile
if [ -n "$DOMAIN" ]; then
  echo "SETLEDGER_DOMAIN=$DOMAIN" > /etc/default/caddy
  mkdir -p /etc/systemd/system/caddy.service.d
  printf '[Service]\nEnvironmentFile=/etc/default/caddy\n' > /etc/systemd/system/caddy.service.d/env.conf
fi
systemctl daemon-reload
systemctl enable --now setledger
systemctl restart setledger caddy
echo "bootstrap ok"
