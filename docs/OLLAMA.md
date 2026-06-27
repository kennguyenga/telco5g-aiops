# Running with Local Ollama (No API Costs)

> **Note:** the stack defaults to the keyless **mock** provider (no setup, no GPU). Use Ollama only if you want a real local model.

This guide covers running the LLM Agent with **local Ollama** instead of the Anthropic API. Tradeoff: free + private, but lower quality and slower than Claude.

## When to use Ollama vs Claude

| You want | Use |
|----------|-----|
| Zero ongoing cost | **Ollama** |
| Run completely offline / no external API | **Ollama** |
| Best diagnosis quality | **Claude** |
| Fast (sub-second) responses | **Claude** |
| Just trying the project locally | **Ollama** |

## Hardware requirements

Ollama needs to run a 7-8B parameter LLM in memory. That requires:

| Setup | RAM | Speed | Notes |
|-------|-----|-------|-------|
| **Minimum** | 6 GB free | 30-60s/turn | Painful but works |
| **Recommended** | 8-12 GB free | 10-20s/turn | OK for demos |
| **Comfortable** | 16+ GB | 5-15s/turn | Smooth |
| **GPU host** | Any GPU + 8 GB VRAM | 1-3s/turn | Best |

**Your local laptop**: probably fine if you have 16+ GB RAM. Close other apps first.

**Your VPS**: a 2 GB Hetzner CPX11 cannot run Ollama — it'll OOM. You need to upgrade to:
- Hetzner **CPX31** (8 GB RAM) — $14.40/mo — **minimum recommended**
- Hetzner **CPX41** (16 GB RAM) — $25.92/mo — comfortable

In the Hetzner Console: your server → **Rescale** → pick CPX31 → confirm. It's an in-place resize (no data loss), takes about 2 minutes.

## Setup — one line

If you've already got the project running with Docker:

```bash
# In the project root, edit .env (create if needed):
echo "LLM_PROVIDER=ollama" >> .env

# Restart the stack:
docker compose down
docker compose up -d --build
```

That's it. The `ollama` and `ollama_init` containers will start. The `ollama_init` container downloads `llama3.1:8b` (~4.7 GB) on first run, then exits.

**First start takes ~5-10 minutes** for the model download. Subsequent starts are instant — the model is cached in a Docker volume.

## Verify it's working

```bash
# 1. Check Ollama is reachable
docker compose exec ollama ollama list
# Should show: llama3.1:8b

# 2. Check llm_agent picked up the right provider
curl http://localhost:19003/healthz
# Should return: {"provider":"ollama", "model":"llama3.1:8b", "ollama_reachable":true, "model_loaded":true}

# 3. In the UI, the LLM Agent tab shows the provider badge:
#    Should display "◉ OLLAMA (LOCAL) llama3.1:8b" with ✓ Ready
```

## Switching back to Claude

```bash
# In .env:
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-haiku-4-5  # cheap, $0.005/run; or claude-sonnet-4-5 for better quality

# Restart just the agent:
docker compose restart llm_agent
```

The Ollama containers keep running in the background but won't be used. To stop them and free RAM:

```bash
docker compose stop ollama ollama_init
```

## Tuning

### Use a different model

Llama 3.1 8B is the default. Other small models you can try:

| Model | Size | Notes |
|-------|------|-------|
| `llama3.1:8b` | 4.7 GB | Default, well-rounded |
| `qwen2.5:7b` | 4.4 GB | Often better at structured output |
| `mistral:7b` | 4.1 GB | Fast, less reliable on tool use |
| `llama3.2:3b` | 2.0 GB | Smaller, fits in 4 GB RAM, weaker reasoning |
| `phi3.5:3.8b` | 2.2 GB | Microsoft, decent for size |

In `.env`:
```bash
OLLAMA_MODEL=qwen2.5:7b
```

After restart, the `ollama_init` container will pull the new model on next start.

### Make it faster

```bash
# Increase the number of CPU threads Ollama uses
docker compose exec ollama sh -c 'echo "OLLAMA_NUM_THREADS=4" >> /etc/environment'

# Or pin specific CPU cores in docker-compose.yml under the ollama service:
#   cpuset: "0-3"
```

For real speed: deploy on a server with a GPU. Hetzner doesn't offer GPUs cheaply. Consider:
- Vast.ai — rent GPU hours from $0.20/hr
- Runpod — similar
- A used eGPU + your laptop

### Reduce timeouts

Default is 180s per LLM call (Llama on CPU is slow). If you're on faster hardware:
```bash
OLLAMA_TIMEOUT=60
```

## Troubleshooting

### "ollama unreachable"
```bash
docker compose ps ollama
docker compose logs ollama
```
Common: not enough RAM. `docker stats` to check. If RAM is the issue, upgrade VPS or use a smaller model.

### "Model not loaded"
```bash
# Manually pull the model
docker compose exec ollama ollama pull llama3.1:8b
```

### Agent gives terrible diagnoses
Llama 8B isn't Claude. Expectations:
- Tool calls succeed maybe 70-80% of the time (vs 99% for Claude)
- Sometimes invents tool arguments
- Multi-step reasoning weakens after 3-4 calls
- Sometimes gets stuck repeating the same tool

Mitigations:
- Lower `max_iterations` to 4 (less chance of going off the rails)
- Use `qwen2.5:7b` instead — often better at structured output
- For real demos, switch to Claude Haiku — it's only $0.005 per agent run

### "Out of memory" / containers OOM-killed
```bash
docker stats
# If ollama is using >6GB and there's no headroom, upgrade VPS or use llama3.2:3b
```

### Agent takes forever
Llama 8B on CPU is slow — 30-60 seconds per turn is normal. With 6 iterations, an agent run takes 3-6 minutes. To check progress, watch the llm_agent logs:
```bash
docker compose logs -f llm_agent
```

## Resource consumption

For the full 14-container stack with Ollama running Llama 3.1 8B:

| Resource | Idle | During agent run |
|----------|------|------------------|
| RAM | ~5 GB | ~6.5 GB |
| CPU | <5% | 100% (all cores) |
| Disk | ~8 GB after model pull | grows ~10 MB/day |

Without the LLM agent running, idle is ~700 MB.

## Cost comparison (monthly, ongoing)

| Setup | VPS | LLM | Total |
|-------|-----|-----|-------|
| **Ollama on CPX31** | $14.40 | $0 | **$14.40** |
| Ollama on CPX41 | $25.92 | $0 | $25.92 |
| Claude Haiku on CPX11 | $5.18 | $1-3 | $6-8 |
| Claude Sonnet on CPX11 | $5.18 | $20-50 | $25-55 |

**Honest take**: if you can afford $7/mo, Claude Haiku is better than Ollama in every dimension except privacy. If you really want $0 LLM cost or need offline, Ollama on CPX31 is the path.
