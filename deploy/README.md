# Deploying 5G AIOps to a VPS

This guide walks you from "I have a credit card" to "I have a working HTTPS demo at https://aiops.mydomain.com" in about 30 minutes.

> **Fedora / RHEL?** See [`DEPLOY_FEDORA.md`](./DEPLOY_FEDORA.md) — it covers the
> `dnf` / `firewalld` / SELinux differences and a one-command `bootstrap-fedora.sh`.
> **No LLM key?** None is needed — the stack defaults to the keyless **mock**
> provider. Add a real provider only if you want a live model.

## Why a VPS instead of Render/Vercel/etc?

For this project specifically:
- **11 microservices.** Render Free's cold starts make demos painful. Render Starter is $7/service × 11 = $77/month.
- **In-memory state.** Subscriber blocks, scenario history, telemetry buffers all live in RAM. PaaS restarts wipe them.
- **Multi-container orchestration.** docker-compose works on a VPS exactly as it does locally. No translation layer.

A single $5-6/mo VPS runs the whole thing 24/7, no cold starts, persistent state across reboots, and the deploy is just `docker compose up`.

---

## Prerequisites

- A VPS provider account. Recommended:
  - **Hetzner** — $5.18/mo CPX11 (2 GB RAM, 2 vCPU). Best value. https://www.hetzner.com/cloud
  - **DigitalOcean** — $6/mo Basic (1 GB RAM). Famously good UX. https://www.digitalocean.com/
  - **Linode** — $5/mo Nanode (1 GB RAM). Akamai-backed. https://www.linode.com/
- A GitHub account with this repo pushed.
- (Optional but recommended) A domain name. Even a $1/yr `.xyz` works.

---

## Step 1: Create the VPS (3 minutes)

### Hetzner (my pick)

1. Sign up at https://www.hetzner.com/cloud
2. **New project** → **Add Server**
3. **Image**: Ubuntu 24.04 LTS
4. **Type**: **CPX11** ($5.18/mo, x86, 2 vCPU, 2 GB RAM)
5. **Location**: Ashburn (nearest to you in VA) or Falkenstein for EU
6. **SSH key**: paste your public key (`cat ~/.ssh/id_ed25519.pub` or generate one with `ssh-keygen`)
7. **Create**

You'll get an IP address. Save it.

### DigitalOcean

1. **Create** → **Droplets**
2. **Image**: Ubuntu 24.04 (LTS) x64
3. **Size**: Basic / Regular / **$6/mo (1 GB RAM)**. *Bump to $12 if you want headroom — this project on 1 GB will swap under load.*
4. **Datacenter**: NYC1 or NYC3 (closest to Ashburn)
5. **Authentication**: SSH key
6. **Create**

---

## Step 2: First SSH (1 minute)

```bash
ssh root@YOUR_SERVER_IP
```

If this is your first SSH ever, you may need to generate a key:
```bash
# On your laptop
ssh-keygen -t ed25519 -C "your-email@example.com"
cat ~/.ssh/id_ed25519.pub
# Paste that into the VPS provider's SSH key field
```

---

## Step 3: Bootstrap the stack (5 minutes)

On the VPS, paste this — it does *everything*: updates the OS, installs Docker, sets up a firewall, creates a service user, clones your repo, configures auto-start on reboot, and shows you the next-step instructions.

```bash
# Replace with your fork URL if different
export REPO_URL=https://github.com/kennguyenga/aiops5g.git
curl -fsSL https://raw.githubusercontent.com/kennguyenga/aiops5g/main/deploy/bootstrap.sh | bash
```

If you haven't pushed to GitHub yet, do that first:
```bash
# On your laptop
cd C:\Users\kenng\Documents\aiops5gv9\aiops5g
git init -b main
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/kennguyenga/aiops5g.git
git push -u origin main
```

The bootstrap script prints a summary at the end — read it.

---

## Step 4: Set your Anthropic key (1 minute)

```bash
sudo nano /opt/aiops5g/.env
```

Paste:
```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Save (Ctrl+X, Y, Enter).

---

## Step 5: Start the stack (5 minutes)

```bash
sudo systemctl start aiops5g
```

This kicks off a `docker compose up -d --build`. First build is ~5 minutes. Watch progress:

```bash
sudo -u aiops docker compose -f /opt/aiops5g/docker-compose.yml logs -f
```

When you see `Application startup complete` for all NFs, you're up. Press Ctrl+C to stop tailing logs.

---

## Step 6: First test (1 minute)

Open `http://YOUR_SERVER_IP:5173` in a browser.

You should see the dashboard with the green pulsing dot, all 7 NFs in Topology eventually going green, and 9 sidebar tabs.

If it doesn't load: see [Troubleshooting](#troubleshooting) below.

---

## Step 7: HTTPS with your domain (10 minutes)

This step replaces `http://1.2.3.4:5173` with `https://aiops.yourdomain.com`. Free, automated, auto-renewing.

### 7a. Point a domain at the VPS

In your DNS provider (Cloudflare / Namecheap / wherever):

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A    | aiops | YOUR_SERVER_IP | 600 |

Or use a subdomain like `demo.kennguyen.dev`.

Wait 5 minutes for DNS to propagate. Verify:
```bash
dig +short aiops.kennguyen.dev   # should print your VPS IP
```

### 7b. Run the HTTPS setup

```bash
sudo bash /opt/aiops5g/deploy/setup-https.sh aiops.kennguyen.dev your-email@example.com
```

This:
1. Installs nginx + certbot
2. Configures nginx to reverse-proxy port 80 → port 5173 (the frontend container)
3. Issues a Let's Encrypt cert for your domain
4. Sets up HTTP→HTTPS redirect
5. Enables 60-day auto-renewal via systemd timer

Done. Visit `https://aiops.kennguyen.dev` — clean URL, valid cert, no port number.

---

## Operations cheat sheet

```bash
# Status of the whole stack
sudo systemctl status aiops5g
cd /opt/aiops5g && sudo -u aiops docker compose ps

# Logs (all services or one)
sudo -u aiops docker compose -f /opt/aiops5g/docker-compose.yml logs -f
sudo -u aiops docker compose -f /opt/aiops5g/docker-compose.yml logs -f orchestrator

# Restart stack
sudo systemctl restart aiops5g

# Stop stack (containers stay built, just not running)
sudo systemctl stop aiops5g

# Pull latest code from GitHub + rebuild + restart
cd /opt/aiops5g
sudo -u aiops git pull
sudo -u aiops docker compose up -d --build

# Disk usage of Docker
sudo docker system df

# Clean up old images (after rebuilds)
sudo docker image prune -a -f
```

### Clean redeploy (when things are broken)

If something's gone wrong — wrong code on disk, orphaned containers from an old project name, can't figure out the state — use the redeploy script:

```bash
sudo bash /opt/aiops5g/deploy/redeploy.sh
```

This:
1. Stops all aiops5g containers
2. Removes any leftover/orphaned containers from previous deploys
3. Backs up your `.env` (so you don't lose your API key)
4. Wipes `/opt/aiops5g` and re-clones from GitHub
5. Restores your `.env`
6. Prunes Docker cache
7. Rebuilds and starts the stack

Use this whenever you've changed providers (Ollama → Gemini), upgraded the project version, or just want a clean state.

### Switching LLM providers

```bash
sudo nano /opt/aiops5g/.env
# Change LLM_PROVIDER=gemini to LLM_PROVIDER=anthropic (or ollama)
# Add the appropriate API key

cd /opt/aiops5g
sudo -u aiops docker compose restart llm_agent

# Verify
curl -s http://localhost:19003/healthz
```

The frontend automatically detects the new provider — you don't need to redeploy the SPA.

---

## Resource expectations

For the 11-container stack on a 2 GB VPS:

| Metric | Idle | Under load (10 UEs/s) |
|--------|------|----------------------|
| RAM    | ~700 MB | ~1.1 GB |
| CPU    | <5% (mostly Docker daemon) | 25-40% |
| Disk   | ~3 GB after build | grows ~10 MB/day with logs |

A 1 GB VPS will work but may swap under load — fine for a portfolio demo, not for live traffic. 2 GB is the sweet spot.

---

## Cost summary

| Item | Cost |
|------|------|
| Hetzner CPX11 VPS | $5.18/mo |
| Domain (`.xyz` from Cloudflare/Porkbun) | ~$1-3/year |
| Let's Encrypt cert | $0 |
| Anthropic API (light demo use) | $5-10/mo at typical demo rate |
| **Total** | **~$10-15/mo** |

You can park the VPS or pause it (stop the systemd unit) when you're not actively demoing — Hetzner only charges per hour you're running.

---

## Troubleshooting

### Stack won't start
```bash
sudo journalctl -u aiops5g -n 100
cd /opt/aiops5g && sudo -u aiops docker compose ps
sudo -u aiops docker compose logs orchestrator | tail -30
```

### Port 5173 not reachable from internet
The bootstrap firewall blocks all ports except 22, 80, 443. Either:
- Run setup-https.sh and use a domain (recommended)
- Or temporarily open 5173: `sudo ufw allow 5173`

### "Out of memory" / containers OOM-killed
You're on a 1 GB VPS under load. Either upgrade the VPS or reduce the stack — easiest is to skip ML+LLM:
```bash
cd /opt/aiops5g
sudo -u aiops docker compose stop ml_engine llm_agent
```

### LLM Agent says "ANTHROPIC_API_KEY not configured"
Did you put it in `/opt/aiops5g/.env`?  Did you restart the stack after?
```bash
sudo nano /opt/aiops5g/.env
sudo systemctl restart aiops5g
```

### Need to start over completely
```bash
sudo systemctl stop aiops5g
cd /opt/aiops5g
sudo -u aiops docker compose down -v
sudo docker system prune -af
sudo systemctl start aiops5g
```

---

## Security notes

The bootstrap script does the basics: UFW firewall, non-root service user, restricted env file permissions. For a portfolio demo this is fine. **Don't put real production data on this** — there's no auth on the API, the toy crypto is genuinely toy, and the LLM agent has unrestricted ability to inject and clear faults.

If you want to add basic auth in front of the dashboard, add this to the nginx config that `setup-https.sh` writes:
```nginx
auth_basic "5G AIOps";
auth_basic_user_file /etc/nginx/.htpasswd;
```
Then `sudo apt install apache2-utils && sudo htpasswd -c /etc/nginx/.htpasswd kenny`.

---

## What you get when this is done

- A live URL like `https://aiops.kennguyen.dev` you can put on your resume / LinkedIn
- 24/7 uptime, no cold starts
- Auto-restart after reboots
- HTTPS cert auto-renewing
- One-command updates from GitHub

Total setup time: ~30 minutes. Total ongoing cost: ~$5/mo + tiny domain + tiny Anthropic usage.
