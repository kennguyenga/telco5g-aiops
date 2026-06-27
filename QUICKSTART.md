# Quick Start

A simulated 5G core (7 network functions), an ML failure classifier, and an
LLM SRE agent that investigates and remediates injected faults — all behind a
React dashboard.

## Run it (no API key required)

The agent defaults to a **keyless mock provider** — a deterministic SRE
playbook that uses the real tools to investigate and fix faults. So a fresh
install runs end-to-end with nothing to configure.

```bash
docker compose up -d --build        # first build ~5 min
# open http://localhost:5173
```

That's the whole happy path. From the dashboard you can inject faults, watch
the topology and telemetry react, run the ML classifier, and click the agent's
"remediate" button — the mock will investigate, clear the faults, and verify.

## Deploying on Fedora / RHEL

See **[deploy/DEPLOY_FEDORA.md](deploy/DEPLOY_FEDORA.md)** for the full guide,
or run the bootstrap on a fresh host:

```bash
sudo bash deploy/bootstrap-fedora.sh
sudo systemctl start aiops5g
```

(For Ubuntu, the original `deploy/bootstrap.sh` + `deploy/README.md` still apply.)

## Using a real LLM instead of the mock

Create a `.env` next to `docker-compose.yml`:

```bash
# Google Gemini — free tier, low RAM (key: https://aistudio.google.com/apikey)
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
```

Other providers: `ollama` (local, ~8 GB RAM) or `anthropic` (paid). If you set
a provider but leave its key blank, the agent falls back to the mock
automatically; set `LLM_FALLBACK_MOCK=0` to turn that off and get a hard error
instead.

## Ports

- Dashboard: `5173`
- Network functions (debug): `18001`–`18007`
- Control plane (collector / orchestrator / ml / agent): `19000`–`19003`
