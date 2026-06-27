#!/bin/bash
# ============================================================================
# 5G AIOps — Fedora / RHEL Bootstrap Script
# ============================================================================
# Takes a fresh Fedora (39/40/41+) or RHEL-family (RHEL/Rocky/Alma 9+) host
# and brings it to a running aiops5g stack. Idempotent — safe to re-run.
#
# Differences from the Ubuntu bootstrap:
#   • dnf instead of apt
#   • Docker CE from the Fedora repo (Fedora ships Podman by default)
#   • firewalld instead of UFW
#   • SELinux is enforcing by default — handled where it matters
#
# Usage on a fresh host (run as root / via sudo):
#   sudo REPO_URL=https://github.com/<you>/aiops5g.git bash deploy/bootstrap-fedora.sh
#
# Or, if the code is already on the box, run it from the project root:
#   sudo bash deploy/bootstrap-fedora.sh
# ============================================================================
set -euo pipefail

# ── Config (override via env) ────────────────────────────────────────
# By default the script installs from the unzipped project it lives in (no
# git needed). Set REPO_URL only if you want it to git-clone instead.
REPO_URL="${REPO_URL:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/aiops5g}"
SERVICE_USER="${SERVICE_USER:-aiops}"
# Open the frontend port directly (for LAN/local demos). Set to 0 if you'll
# put nginx + HTTPS in front instead and only want 80/443 exposed.
OPEN_FRONTEND_PORT="${OPEN_FRONTEND_PORT:-1}"

# ── Color helpers ────────────────────────────────────────────────────
log()  { printf "\e[1;32m[+] %s\e[0m\n" "$*"; }
warn() { printf "\e[1;33m[!] %s\e[0m\n" "$*"; }
err()  { printf "\e[1;31m[x] %s\e[0m\n" "$*" >&2; exit 1; }

# ── Sanity checks ────────────────────────────────────────────────────
[[ "$EUID" -eq 0 ]] || err "Run as root (use sudo)"
. /etc/os-release
case "${ID}${ID_LIKE:-}" in
    *fedora*|*rhel*|*centos*) log "Detected ${PRETTY_NAME} — proceeding" ;;
    *) err "This script targets Fedora/RHEL-family (yours: $ID). Use bootstrap.sh for Ubuntu." ;;
esac

# ── 1. System update + base packages ─────────────────────────────────
log "Step 1/7 — System update"
dnf -y upgrade --refresh
dnf -y install git make jq curl ca-certificates firewalld policycoreutils-python-utils

# ── 2. Firewall (firewalld) ──────────────────────────────────────────
log "Step 2/7 — Firewall (firewalld)"
systemctl enable --now firewalld
firewall-cmd --permanent --add-service=ssh
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
if [[ "$OPEN_FRONTEND_PORT" == "1" ]]; then
    # Direct access to the dashboard at http://<host>:5173 (LAN/local demos).
    firewall-cmd --permanent --add-port=5173/tcp
fi
firewall-cmd --reload
firewall-cmd --list-all | head -20

# ── 3. Docker CE (from the Fedora repo) ──────────────────────────────
# Fedora's default container engine is Podman; we install Docker CE so the
# existing docker-compose.yml runs unchanged. (Podman alternative noted in
# DEPLOY_FEDORA.md.)
log "Step 3/7 — Docker CE"
if ! command -v docker &>/dev/null; then
    dnf -y install dnf-plugins-core
    # Fetch the repo file directly so this works on both dnf4 and dnf5
    # (the `config-manager --add-repo` syntax changed in dnf5 / Fedora 41+).
    curl -fsSL https://download.docker.com/linux/fedora/docker-ce.repo \
        -o /etc/yum.repos.d/docker-ce.repo
    dnf -y install docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
fi
docker --version
docker compose version

# ── 4. Service user ──────────────────────────────────────────────────
log "Step 4/7 — Service user '$SERVICE_USER'"
id -u "$SERVICE_USER" &>/dev/null || useradd -m -s /bin/bash "$SERVICE_USER"
usermod -aG docker "$SERVICE_USER"

# ── 5. Get the code into $INSTALL_DIR ────────────────────────────────
# Created as root (fixes the "/opt: Permission denied" the service user hits),
# then chowned to the service user at the end.
log "Step 5/7 — Code at $INSTALL_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"   # deploy/ -> project root
mkdir -p "$INSTALL_DIR"

if [[ -f "$PROJECT_DIR/docker-compose.yml" && "$PROJECT_DIR" != "$INSTALL_DIR" ]]; then
    # Running from an unzipped copy — install from it, no git required.
    log "Installing from local copy at $PROJECT_DIR"
    cp -a "$PROJECT_DIR/." "$INSTALL_DIR/"
elif [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating existing git checkout"
    git -C "$INSTALL_DIR" pull --ff-only || warn "git pull skipped"
elif [[ -f "$INSTALL_DIR/docker-compose.yml" ]]; then
    warn "$INSTALL_DIR already has the code — leaving it as-is"
elif [[ -n "$REPO_URL" ]]; then
    log "Cloning $REPO_URL"
    git clone "$REPO_URL" "$INSTALL_DIR"
else
    err "No local code found and no REPO_URL set. Unzip the project and run this from inside it (sudo bash deploy/bootstrap-fedora.sh)."
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

# ── 6. Environment file ──────────────────────────────────────────────
ENV_FILE="$INSTALL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    log "Creating $ENV_FILE (defaults to Google Gemini Flash — free tier)"
    cat > "$ENV_FILE" <<'EOF'
# ─── LLM provider ──────────────────────────────────────────────
# "mock"       — keyless deterministic SRE playbook (DEFAULT) — no API key
# "gemini"     — Google Gemini Flash, FREE tier (15 req/min) — low RAM
# "ollama"     — local Llama 3.1 8B, free, needs ~8 GB RAM
# "anthropic"  — Claude Haiku/Sonnet, paid
LLM_PROVIDER=mock

# If you switch to a real provider but leave its key blank, the agent falls
# back to the mock automatically. Set LLM_FALLBACK_MOCK=0 to disable that.
LLM_FALLBACK_MOCK=1

# Gemini (free key: https://aistudio.google.com/apikey) — only if LLM_PROVIDER=gemini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-1.5-flash

# Ollama (only if LLM_PROVIDER=ollama; run with: docker compose --profile ollama up -d)
OLLAMA_MODEL=llama3.1:8b

# Anthropic (only if LLM_PROVIDER=anthropic)
# ANTHROPIC_API_KEY=sk-ant-...
# CLAUDE_MODEL=claude-haiku-4-5
EOF
    chmod 600 "$ENV_FILE"
    chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
fi

# ── 7. systemd unit for auto-start on reboot ─────────────────────────
log "Step 6/7 — systemd unit"
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
ExecStart=/usr/bin/docker compose up -d --build --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=900
User=$SERVICE_USER
Group=$SERVICE_USER

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable aiops5g.service

# ── Summary ──────────────────────────────────────────────────────────
log "Step 7/7 — Bootstrap complete"
PUBLIC_IP=$(curl -fsSL https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
cat <<DONE

============================================================
  5G AIOps bootstrap complete on ${PRETTY_NAME}
============================================================
Install path:  $INSTALL_DIR
Service user:  $SERVICE_USER
Host IP:       $PUBLIC_IP

NEXT STEPS
  1) (Optional) Pick a real LLM:  sudo nano $INSTALL_DIR/.env
                         Default is keyless mock — no key needed. To use a real
                         provider, set LLM_PROVIDER + its key.
  2) Start the stack:    sudo systemctl start aiops5g     # first build ~5 min
  3) Watch it come up:   cd $INSTALL_DIR && sudo -u $SERVICE_USER docker compose logs -f
  4) Open the dashboard: http://$PUBLIC_IP:5173

OPERATIONS
  Status:   sudo systemctl status aiops5g
  Restart:  sudo systemctl restart aiops5g
  Stop:     sudo systemctl stop aiops5g
  Logs:     cd $INSTALL_DIR && sudo -u $SERVICE_USER docker compose logs -f
DONE
