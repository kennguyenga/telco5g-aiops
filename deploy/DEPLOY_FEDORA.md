# Deploying 5G AIOps on Fedora / RHEL

The stack is plain Docker Compose: one image, `SERVICE_NAME` selects which
service a container runs, and Compose brings up all 12 services + frontend.
Nothing about that is OS-specific. Only four things differ from the Ubuntu
guide: the package manager (`dnf`), the Docker repo, the firewall
(`firewalld`), and **SELinux**, which is enforcing by default on Fedora/RHEL.

> Heads-up: this deploys the **V2 stack** (the one with runnable services).
> The V3 rewrite currently ships only the `nf_common` package, so it has
> nothing to run yet — but the deploy model is identical, so this guide
> applies unchanged once the V3 services are built.

---

## Prerequisites

- A Fedora 39/40/41+ or RHEL-family 9+ (Rocky/Alma) host — local workstation,
  VM, or server. ~2 GB RAM is comfortable for Gemini/Anthropic providers;
  budget ~8 GB if you want the local Ollama provider.
- The project code on that host (either `git clone` your repo, or copy the
  zip and `unzip` it).
- **No LLM key needed** — the stack ships defaulting to the keyless `mock`
  provider (a deterministic SRE playbook). To use a real model instead, set
  `LLM_PROVIDER` + its key in `.env` (free Gemini key from
  https://aistudio.google.com/apikey).

---

## Option A — Automated (one script)

From the project root on the Fedora host:

```bash
sudo bash deploy/bootstrap-fedora.sh
```

It updates the system, opens the firewall, installs Docker CE, creates an
`aiops` service user, writes a `.env`, and installs a systemd unit that
auto-starts the stack on boot. Then:

```bash
sudo nano /opt/aiops5g/.env        # set GEMINI_API_KEY=...
sudo systemctl start aiops5g       # first build ~5 min
cd /opt/aiops5g && sudo -u aiops docker compose logs -f
```

Open `http://<host-ip>:5173`.

---

## Option B — Manual (understand each step)

### 1. Install Docker CE

Fedora ships **Podman**, not Docker, so install Docker CE from Docker's Fedora
repo. (Fetching the `.repo` file directly works on both dnf4 and dnf5 — the
`dnf config-manager --add-repo` syntax changed in Fedora 41.)

```bash
sudo dnf -y install dnf-plugins-core
sudo curl -fsSL https://download.docker.com/linux/fedora/docker-ce.repo \
     -o /etc/yum.repos.d/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io \
     docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
docker compose version           # confirm the compose plugin is present
```

Optional — run docker without sudo:

```bash
sudo usermod -aG docker "$USER"
newgrp docker                    # or log out/in
```

### 2. Open the firewall (firewalld, not UFW)

```bash
sudo firewall-cmd --permanent --add-port=5173/tcp   # the dashboard
sudo firewall-cmd --reload
```

For a public server behind nginx, open `http`/`https` instead and skip 5173.

### 3. Build and start

```bash
cd /path/to/aiops5g                 # where docker-compose.yml lives
docker compose up -d --build        # first build ~5 min; defaults to keyless mock
docker compose ps
```

Visit `http://<host-ip>:5173`. To use a real model instead of the mock,
create `.env` first:

```bash
printf 'LLM_PROVIDER=gemini\nGEMINI_API_KEY=YOUR_KEY\n' > .env
```

---

## SELinux notes

This compose file uses **named volumes** (Docker-managed) and no host
bind-mounts at runtime, so SELinux in enforcing mode does **not** block it —
no changes needed. Leave SELinux on.

The two cases where SELinux *would* matter:

- **If you add a host bind-mount** (e.g. `-v ./data:/app/data`), append a `:Z`
  label so Docker relabels it: `-v ./data:/app/data:Z`. Without it the
  container gets `permission denied`.
- **If you front the stack with nginx** as a reverse proxy (below), SELinux
  blocks nginx from making outbound connections until you flip one boolean.

Check SELinux state with `getenforce` (expect `Enforcing`).

---

## Optional — public HTTPS with nginx

The repo's `setup-https.sh` is apt-based; on Fedora do the equivalent:

```bash
sudo dnf -y install nginx certbot python3-certbot-nginx
sudo setsebool -P httpd_can_network_connect 1   # <-- the SELinux gotcha
```

That boolean is the one step people miss: without it nginx returns 502
because SELinux forbids the proxy connection to the frontend container.
Then configure an nginx server block proxying `:80` → `127.0.0.1:5173`,
issue the cert with `sudo certbot --nginx -d your.domain`, and open
`http`/`https` in firewalld.

---

## Podman instead of Docker (the Fedora-native path)

If you'd rather use Fedora's built-in engine:

```bash
sudo dnf -y install podman podman-compose
podman compose up -d --build        # Podman 4.4+ understands compose files
```

Most of this compose file works under rootful Podman. Caveats: the
`depends_on: condition: service_healthy` gates and `profiles` (the optional
Ollama services) have weaker support than under Docker CE, so Docker CE is the
lower-friction choice for this particular stack. Use Podman if avoiding the
Docker daemon matters to you.

---

## Operations

```bash
sudo systemctl status aiops5g                       # if installed via bootstrap
sudo systemctl restart aiops5g
cd /opt/aiops5g && sudo -u aiops docker compose logs -f orchestrator

# switch LLM provider
sudo nano /opt/aiops5g/.env                          # LLM_PROVIDER=anthropic + key
cd /opt/aiops5g && sudo -u aiops docker compose restart llm_agent

# full reset
cd /opt/aiops5g && sudo -u aiops docker compose down -v && sudo docker system prune -af
```

---

## Quick troubleshooting

- **Dashboard unreachable from another machine** — the firewall port isn't
  open: `sudo firewall-cmd --permanent --add-port=5173/tcp && sudo firewall-cmd --reload`.
  (localhost on the same box is never firewalled.)
- **`permission denied` on a bind-mount** — missing `:Z` label (see SELinux).
- **nginx 502 behind the proxy** — `sudo setsebool -P httpd_can_network_connect 1`.
- **Containers OOM-killed** — you're likely on the Ollama provider with < 8 GB
  RAM. Switch `LLM_PROVIDER=gemini` (or `anthropic`) in `.env` and restart.
- **`docker: command not found` after install** — start the daemon:
  `sudo systemctl enable --now docker`.
