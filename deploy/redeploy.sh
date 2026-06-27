#!/bin/bash
# ============================================================================
# 5G AIOps — Clean Redeploy Script
# ============================================================================
# Wipes existing aiops5g containers and any stale code, then pulls latest
# from GitHub and rebuilds. Safe to run repeatedly. Use this when:
#   - Upgrading from an older version
#   - Switching LLM providers
#   - Recovering from a broken state
#
# Usage on VPS:
#   sudo bash /opt/aiops5g/deploy/redeploy.sh [REPO_URL]
#
# If REPO_URL is omitted, uses the current origin from /opt/aiops5g/.
# ============================================================================
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/aiops5g}"
SERVICE_USER="${SERVICE_USER:-aiops}"
REPO_URL="${1:-}"

log() { printf "\e[1;32m[+] %s\e[0m\n" "$*"; }
warn() { printf "\e[1;33m[!] %s\e[0m\n" "$*"; }
err() { printf "\e[1;31m[✗] %s\e[0m\n" "$*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || err "Run as root (use sudo)"

# ── 1. Stop and remove existing containers ───────────────────────────
log "Step 1/6 — Stopping current stack"
if [[ -f "$INSTALL_DIR/docker-compose.yml" ]]; then
    cd "$INSTALL_DIR"
    sudo -u "$SERVICE_USER" docker compose down --remove-orphans 2>/dev/null || true
fi

# Hunt down any leftover aiops5g containers (e.g. from old project name)
leftover=$(docker ps -a --format '{{.Names}}' | grep -E '^(aiops5g|netops|ollama)' || true)
if [[ -n "$leftover" ]]; then
    warn "Removing leftover containers: $leftover"
    docker rm -f $leftover 2>/dev/null || true
fi

# ── 2. Save current .env (so we don't lose the API key) ──────────────
ENV_FILE="$INSTALL_DIR/.env"
ENV_BACKUP="/tmp/aiops5g.env.backup.$(date +%s)"
if [[ -f "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "$ENV_BACKUP"
    log "Backed up existing .env to $ENV_BACKUP"
fi

# ── 3. Determine repo URL ────────────────────────────────────────────
if [[ -z "$REPO_URL" ]]; then
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        REPO_URL=$(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" git remote get-url origin 2>/dev/null || echo "")
    fi
fi

[[ -n "$REPO_URL" ]] || err "No REPO_URL given and none found in existing repo. Pass as: sudo bash redeploy.sh https://github.com/USER/aiops5g.git"

log "Step 2/6 — Using repo: $REPO_URL"

# ── 4. Wipe and re-clone ─────────────────────────────────────────────
log "Step 3/6 — Removing old code at $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
chown "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

log "Step 4/6 — Cloning fresh from $REPO_URL"
sudo -u "$SERVICE_USER" git clone "$REPO_URL" "$INSTALL_DIR"

# ── 5. Restore .env ─────────────────────────────────────────────────
if [[ -f "$ENV_BACKUP" ]]; then
    cp "$ENV_BACKUP" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
    log "Restored .env from backup"
else
    log "No previous .env found — creating default (Gemini provider)"
    cat > "$ENV_FILE" <<EOF
LLM_PROVIDER=gemini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-1.5-flash
EOF
    chmod 600 "$ENV_FILE"
    chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
    warn "Edit $ENV_FILE to set GEMINI_API_KEY before starting!"
fi

# ── 6. Clean Docker caches (frees space, ensures fresh build) ────────
log "Step 5/6 — Pruning Docker caches"
docker system prune -f 2>/dev/null || true

# ── 7. Build and start ──────────────────────────────────────────────
log "Step 6/6 — Building and starting stack"
cd "$INSTALL_DIR"
sudo -u "$SERVICE_USER" docker compose build
sudo -u "$SERVICE_USER" docker compose up -d

# ── 8. Summary ──────────────────────────────────────────────────────
PUBLIC_IP=$(curl -fsSL https://api.ipify.org || echo "<your-ip>")

cat <<DONE

╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║   Redeploy complete                                            ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝

Server IP:      $PUBLIC_IP
Install path:   $INSTALL_DIR
.env backup:    $ENV_BACKUP

Verify status:
  cd $INSTALL_DIR && sudo -u $SERVICE_USER docker compose ps
  curl http://localhost:19003/healthz

Access dashboard:
  http://$PUBLIC_IP:5173        (HTTP, may need: ufw allow 5173)
  https://aiops.kennguyen.dev   (if HTTPS already configured)

Tail logs:
  cd $INSTALL_DIR && sudo -u $SERVICE_USER docker compose logs -f

If LLM_PROVIDER=gemini and you haven't set the key yet:
  sudo nano $ENV_FILE          # paste GEMINI_API_KEY=AIzaSy...
  cd $INSTALL_DIR && sudo -u $SERVICE_USER docker compose restart llm_agent

DONE
