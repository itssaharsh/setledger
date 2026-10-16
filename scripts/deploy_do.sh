#!/usr/bin/env bash
# One-shot DigitalOcean deploy (create or redeploy), driven by doctl.
#   scripts/deploy_do.sh [domain]       e.g. scripts/deploy_do.sh setledger.risp.tech
# Token: ~/.config/doctl/token (0600). Ships exactly `git archive HEAD`, nothing uncommitted.
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="${1:-}"
NAME=setledger
REGION="${SETLEDGER_REGION:-nyc3}"
SIZE="${SETLEDGER_SIZE:-s-1vcpu-2gb}"
IMAGE=ubuntu-24-04-x64
KEY="$HOME/.ssh/setledger_do"
TOKEN_FILE="$HOME/.config/doctl/token"

[ -s "$TOKEN_FILE" ] || { echo "missing $TOKEN_FILE"; exit 1; }
DIGITALOCEAN_ACCESS_TOKEN="$(<"$TOKEN_FILE")"
export DIGITALOCEAN_ACCESS_TOKEN
command -v doctl >/dev/null || { echo "doctl not on PATH"; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "commit first: only HEAD ships"; exit 1; }

# ssh key: local keypair + registered on DO
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C setledger-deploy -f "$KEY"
FP="$(ssh-keygen -E md5 -lf "$KEY.pub" | awk '{print $2}' | sed 's/^MD5://')"
KEY_ID="$(doctl compute ssh-key list --format ID,FingerPrint --no-header | awk -v fp="$FP" '$2==fp{print $1}')"
[ -n "$KEY_ID" ] || KEY_ID="$(doctl compute ssh-key import "$NAME-deploy" --public-key-file "$KEY.pub" \
  --format ID --no-header)"

# droplet: reuse if it exists, else create
DROPLET_ID="$(doctl compute droplet list --format ID,Name --no-header | awk -v n="$NAME" '$2==n{print $1}')"
if [ -z "$DROPLET_ID" ]; then
  echo "creating droplet $NAME ($SIZE, $REGION)…"
  DROPLET_ID="$(doctl compute droplet create "$NAME" --region "$REGION" --size "$SIZE" \
    --image "$IMAGE" --ssh-keys "$KEY_ID" --enable-monitoring --wait --format ID --no-header)"
fi
IP="$(doctl compute droplet get "$DROPLET_ID" --format PublicIPv4 --no-header)"
echo "droplet $DROPLET_ID at $IP"

# firewall: 22/80/443 in, everything out; attached by droplet id (no tag scope needed)
if ! doctl compute firewall list --format Name --no-header | grep -qx "$NAME-fw"; then
  doctl compute firewall create --name "$NAME-fw" --droplet-ids "$DROPLET_ID" \
    --inbound-rules "protocol:tcp,ports:22,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:80,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0" \
    --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0" \
    >/dev/null
fi

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 "root@$IP")
for _ in $(seq 1 40); do "${SSH[@]}" true 2>/dev/null && break; sleep 5; done
"${SSH[@]}" 'cloud-init status --wait >/dev/null 2>&1 || true'

echo "shipping $(git rev-parse --short HEAD)…"
git archive --format=tar HEAD | "${SSH[@]}" 'mkdir -p /opt/setledger && tar -x -C /opt/setledger'
TIGER="$HOME/.config/setledger/tiger_dsn"
if [ -s "$TIGER" ]; then   # the live demo nominates through pgvector on Tiger Data
  printf 'SETLEDGER_PG_DSN="%s"\n' "$(<"$TIGER")" \
    | "${SSH[@]}" 'umask 077; cat > /etc/setledger.env'
fi
"${SSH[@]}" "bash /opt/setledger/deploy/bootstrap.sh '$DOMAIN'"

echo "smoke: waiting for the demo world…"
for _ in $(seq 1 60); do
  curl -fs --max-time 5 "http://$IP/api/state" | grep -q '"ready":true' && { echo "LIVE  http://$IP/"; break; }
  sleep 5
done
[ -n "$DOMAIN" ] && echo "point an A record: $DOMAIN -> $IP (Caddy fetches TLS once it resolves)"
