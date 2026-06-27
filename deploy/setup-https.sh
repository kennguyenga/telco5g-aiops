#!/bin/bash
# ============================================================================
# 5G AIOps — HTTPS Setup
# ============================================================================
# Installs nginx as a reverse proxy in front of the stack, plus a Let's Encrypt
# cert auto-renewing via certbot. Run AFTER bootstrap.sh and AFTER pointing
# your domain's A record at this server's IP.
#
# Usage:
#   sudo bash setup-https.sh your-domain.com [email@example.com]
# ============================================================================
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-admin@$DOMAIN}"

[[ -n "$DOMAIN" ]] || {
    echo "Usage: sudo bash setup-https.sh your-domain.com [email@example.com]"
    exit 1
}

log() { printf "\e[1;32m[+] %s\e[0m\n" "$*"; }

# ── DNS sanity check ─────────────────────────────────────────────────
log "Checking DNS for $DOMAIN..."
DOMAIN_IP=$(dig +short "$DOMAIN" | tail -1)
SERVER_IP=$(curl -fsSL https://api.ipify.org)

if [[ -z "$DOMAIN_IP" ]]; then
    echo "WARNING: $DOMAIN does not resolve. Make sure you've added an A record"
    echo "         pointing $DOMAIN -> $SERVER_IP and DNS has propagated."
    read -p "Continue anyway? [y/N] " -n 1 -r; echo
    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
elif [[ "$DOMAIN_IP" != "$SERVER_IP" ]]; then
    echo "WARNING: $DOMAIN resolves to $DOMAIN_IP, but this server is $SERVER_IP"
    read -p "Continue anyway? [y/N] " -n 1 -r; echo
    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
fi

# ── Install nginx + certbot ──────────────────────────────────────────
log "Installing nginx + certbot"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx certbot python3-certbot-nginx

# ── nginx config ─────────────────────────────────────────────────────
log "Writing nginx config for $DOMAIN"

cat > /etc/nginx/sites-available/aiops5g <<EOF
# 5G AIOps reverse proxy
# HTTPS comes from certbot after this runs

server {
    listen 80;
    server_name $DOMAIN;

    # Increase timeouts — LLM agent calls can take 60+s
    proxy_read_timeout 180s;
    proxy_connect_timeout 30s;
    proxy_send_timeout 60s;

    # Body size — Anthropic message bodies can be large
    client_max_body_size 10M;

    # Frontend (the SPA + its built-in /api/* proxies)
    location / {
        proxy_pass http://127.0.0.1:5173;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # SPA WebSocket support (Vite dev server uses HMR; harmless in prod)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

ln -sf /etc/nginx/sites-available/aiops5g /etc/nginx/sites-enabled/aiops5g
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# ── Issue certificate ────────────────────────────────────────────────
log "Issuing Let's Encrypt certificate"
certbot --nginx \
    --non-interactive --agree-tos --redirect \
    --email "$EMAIL" \
    -d "$DOMAIN"

# ── Auto-renew ───────────────────────────────────────────────────────
log "Verifying auto-renewal"
systemctl status certbot.timer --no-pager | head -5 || \
    systemctl enable --now snap.certbot.renew.timer 2>/dev/null || true
certbot renew --dry-run

cat <<DONE

╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║   HTTPS configured — your stack is live at:                    ║
║                                                                ║
║     https://$DOMAIN                                            ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝

Auto-renewal: certbot renews every 60 days via systemd timer
Verify renewal:   sudo certbot renew --dry-run

DONE
