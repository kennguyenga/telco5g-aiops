#!/bin/bash
# ============================================================================
# 5G AIOps — VPS Bootstrap Script
# ============================================================================
# Takes a fresh Ubuntu 22.04 / 24.04 server and brings it to a running
# aiops5g stack. Idempotent — safe to re-run.
#
# Usage on a fresh VPS:
#   curl -fsSL https://raw.githubusercontent.com/<your-user>/aiops5g/main/deploy/bootstrap.sh | sudo bash
#
# Or after cloning:
#   sudo bash deploy/bootstrap.sh
#
# What it does:
#   1. System updates + firewall (UFW: only 22, 80, 443)
#   2. Install Docker Engine + compose plugin
#   3. Create non-root 'aiops' user with docker group
#   4. Clone (or update) the repo to /opt/aiops5g
#   5. Build & start the stack
#   6. Print next-step instructions for HTTPS + domain
# ============================================================================
set -euo pipefail

# ── Config (override via env) ────────────────────────────────────────
REPO_URL="${REPO_URL:-https://github.com/kennguyenga/aiops5g.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/aiops5g}"
SERVICE_USER="${SERVICE_USER:-aiops}"

# ── Color helpers ────────────────────────────────────────────────────
log() { printf "\e[1;32m[+] %s\e[0m\n" "$*"; }
warn() { printf "\e[1;33m[!] %s\e[0m\n" "$*"; }
err() { printf "\e[1;31m[✗] %s\e[0m\n" "$*" >&2; exit 1; }

# ── Sanity checks ────────────────────────────────────────────────────
[[ "$EUID" -eq 0 ]] || err "Run as root (use sudo)"
. /etc/os-release
[[ "$ID" == "ubuntu" ]] || err "This script targets Ubuntu (yours: $ID)"
log "Detected Ubuntu $VERSION_ID — proceeding"

# ── 1. System update ─────────────────────────────────────────────────
log "Step 1/6 — System update"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates curl gnupg lsb-release ufw git make jq

# ── 2. Firewall ──────────────────────────────────────────────────────
log "Step 2/6 — Firewall"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable
ufw status verbose | head -20

# ── 3. Docker Engine ─────────────────────────────────────────────────
log "Step 3/6 — Docker Engine"
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    cat > /etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable
EOF
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
fi
docker --version
docker compose version

# ── 4. Service user ──────────────────────────────────────────────────
log "Step 4/6 — Service user '$SERVICE_USER'"
if ! id -u "$SERVICE_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$SERVICE_USER"
fi
usermod -aG docker "$SERVICE_USER"

# ── 5. Clone / update repo ───────────────────────────────────────────
log "Step 5/6 — Repo at $INSTALL_DIR"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    cd "$INSTALL_DIR"
    sudo -u "$SERVICE_USER" git pull --ff-only
else
    rm -rf "$INSTALL_DIR"
    sudo -u "$SERVICE_USER" git clone "$REPO_URL" "$INSTALL_DIR"
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

# ── 6. Production env file ───────────────────────────────────────────
ENV_FILE="$INSTALL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    log "Creating $ENV_FILE — defaults to Google Gemini Flash (free tier)"
    cat > "$ENV_FILE" <<EOF
# ─── LLM provider ──────────────────────────────────────────────
# "gemini"     — Google Gemini Flash, FREE tier (15 req/min, 1500/day) ⭐
# "ollama"     — local Llama 3.1 8B, free, but needs 8 GB RAM (not CPX11)
# "anthropic"  — Claude Haiku/Sonnet, costs \$
LLM_PROVIDER=gemini

# ─── Gemini config (when LLM_PROVIDER=gemini) ──────────────────
# Get a FREE key at https://aistudio.google.com/apikey
GEMINI_API_KEY=
GEMINI_MODEL=gemini-1.5-flash

# ─── Ollama config (when LLM_PROVIDER=ollama) ──────────────────
# Requires CPX31+ (8 GB RAM). Run: docker compose --profile ollama up -d
OLLAMA_MODEL=llama3.1:8b

# ─── Anthropic config (when LLM_PROVIDER=anthropic) ────────────
# ANTHROPIC_API_KEY=sk-ant-...
# CLAUDE_MODEL=claude-haiku-4-5
EOF
    chmod 600 "$ENV_FILE"
    chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
fi

# ── 7. systemd unit for auto-start on reboot ─────────────────────────
log "Installing systemd unit for auto-start"
cat > /etc/systemd/system/aiops5g.service <<EOF
[Unit]
Description=5G AIOps Stack
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=600
User=$SERVICE_USER
Group=$SERVICE_USER

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable aiops5g.service

# ── 8. Final summary ─────────────────────────────────────────────────
log "Step 6/6 — Bootstrap complete"
PUBLIC_IP=$(curl -fsSL https://api.ipify.org || echo "<your-ip>")

cat <<DONE

╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║   5G AIOps bootstrap complete                                  ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝

Server IP:     $PUBLIC_IP
Install path:  $INSTALL_DIR
Service user:  $SERVICE_USER

╔══ NEXT STEPS ══════════════════════════════════════════════════╗

⚠️  IMPORTANT: This stack defaults to local Ollama (Llama 3.1 8B).
    Ollama needs ~6 GB RAM. If you're on a 2 GB VPS, either:
      - Upgrade to CPX31 (8 GB) for Ollama, OR
      - Switch to Anthropic API (cheaper if you have <8 GB RAM)

1) Choose your LLM provider — edit $ENV_FILE:

   For local Ollama (default, free, needs 8 GB RAM):
     LLM_PROVIDER=ollama
     # Nothing else to set; model auto-downloads on first start

   For Anthropic Claude (best quality, ~\$0.005/run with Haiku):
     LLM_PROVIDER=anthropic
     ANTHROPIC_API_KEY=sk-ant-...
     CLAUDE_MODEL=claude-haiku-4-5

2) Start the stack:
   sudo systemctl start aiops5g
   # First build: ~5 min
   # First Ollama model download: another ~5 min (~5 GB)

3) Watch progress:
   sudo -u $SERVICE_USER docker compose -f $INSTALL_DIR/docker-compose.yml logs -f

4) Once "Application startup complete" appears for all NFs,
   open:    http://$PUBLIC_IP:5173
   (Direct frontend port — no HTTPS yet)

╔══ HARDENING (recommended) ═════════════════════════════════════╗

5) Set up nginx reverse proxy + Let's Encrypt HTTPS:
   sudo bash $INSTALL_DIR/deploy/setup-https.sh your-domain.com

   This will:
     - Front the stack with nginx on port 80/443
     - Issue a free Let's Encrypt cert
     - Auto-renew via certbot timer
     - Enforce HTTPS

╔══ OPERATIONS ══════════════════════════════════════════════════╗

  Status:    sudo systemctl status aiops5g
  Stop:      sudo systemctl stop aiops5g
  Restart:   sudo systemctl restart aiops5g
  Logs:      cd $INSTALL_DIR && sudo -u $SERVICE_USER docker compose logs -f
  Update:    cd $INSTALL_DIR && sudo -u $SERVICE_USER git pull && \\
             sudo -u $SERVICE_USER docker compose up -d --build

DONE
