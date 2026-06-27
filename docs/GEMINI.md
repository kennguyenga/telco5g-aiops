# Running with Google Gemini Flash (Free)

> **Note:** the stack defaults to the keyless **mock** provider, so you only need this if you want a real LLM. Gemini Flash is the recommended *real* provider.

Gemini Flash is the recommended real provider for this project. Here's why:

| | Gemini Flash | Ollama (local) | Claude Haiku |
|---|---|---|---|
| **Monthly cost** | $0 | $0 LLM + $14 bigger VPS | ~$0.50-2 |
| **Quality** | Good | Decent | Best |
| **Speed** | 2-4s/turn | 5-30s/turn (CPU) | 1-3s/turn |
| **Privacy** | Calls Google | Fully local | Calls Anthropic |
| **Rate limit** | 15 RPM, 1500/day | None | $5 free credit, then pay |

For a portfolio demo clicked occasionally by recruiters, Gemini Flash's 1500 requests/day is plenty. You'll never hit the limit.

## Step 1 — Get a free Gemini API key

1. Go to https://aistudio.google.com/apikey
2. Sign in with your Google account
3. Click **Create API key**
4. Choose your existing Google project, or let it create one for you
5. Copy the key (looks like `AIzaSyD...`)

That's it. No credit card. No verification email.

## Step 2 — Set the key in your `.env`

If your project is on a VPS at `/opt/aiops5g`:

```bash
ssh root@YOUR_VPS_IP
nano /opt/aiops5g/.env
```

Make sure `.env` looks like this:
```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...                  # paste your key here
GEMINI_MODEL=gemini-1.5-flash
```

Save: `Ctrl+X`, `Y`, `Enter`.

## Step 3 — Restart the agent

```bash
cd /opt/aiops5g
sudo -u aiops docker compose restart llm_agent
```

## Step 4 — Verify

```bash
curl -s http://localhost:19003/healthz | python3 -m json.tool
```

Expected output:
```json
{
  "provider": "gemini",
  "status": "ok",
  "model": "gemini-1.5-flash",
  "api_key_configured": true,
  "gemini_reachable": true
}
```

## Step 5 — Use it

Open the dashboard, click **LLM AGENT** tab. Provider panel should show:
```
◉ GEMINI (FREE) gemini-1.5-flash
✓ Ready
```

Click **▶ START AGENT**. Each iteration takes ~2-4 seconds. A typical agent run completes in ~10-20 seconds.

## Rate limits and cost

Free tier limits (as of Jan 2025):
- **15 requests per minute**
- **1500 requests per day**
- **1 million tokens per minute**
- **No daily token limit**

Each agent run = ~5-8 requests (1 per tool call + 1 final). So you can do ~190 agent runs per day before hitting the limit. For a portfolio demo, this is enormous headroom.

If you exceed the limit, the API returns 429 errors and your agent will stop mid-run. Wait a minute and retry.

## Switching models

Gemini offers several models:

| Model | Notes |
|-------|-------|
| `gemini-1.5-flash` | Default. Fast, good for tool use. |
| `gemini-1.5-flash-8b` | Smaller, faster, slightly worse |
| `gemini-1.5-pro` | Higher quality, lower rate limit (2 RPM free) |
| `gemini-2.0-flash-exp` | Experimental, may be unstable |

To switch, edit `.env`:
```bash
GEMINI_MODEL=gemini-1.5-pro
```
Then `docker compose restart llm_agent`.

## Troubleshooting

### "GEMINI_API_KEY not set"
You forgot to paste your key into `.env`, or the file isn't loaded. Check:
```bash
cat /opt/aiops5g/.env
docker compose -f /opt/aiops5g/docker-compose.yml exec llm_agent env | grep GEMINI
```

### "gemini error 400: ... API key not valid"
The key is wrong. Common issues:
- Copied with extra whitespace — re-paste, no spaces
- Key was deleted from Google AI Studio — generate a new one

### "gemini error 429: Resource has been exhausted"
You hit the rate limit. Either:
- Wait a minute (per-minute limit) or until tomorrow (per-day limit)
- Switch to a different Gemini model with higher limits
- Use a different Google account's API key

### Agent gives bad diagnoses
Gemini Flash isn't Claude. Common quality issues:
- Sometimes invents tool call arguments (~5-10% of the time)
- Multi-step reasoning weakens after 4-5 steps
- Final summaries are shorter than Claude's

If quality matters for your demo, switch to Claude Haiku temporarily — same price as Gemini's "free with credit" backup, much better quality.

### "Function calling not supported"
You're using a Gemini model that doesn't support tools. Use `gemini-1.5-flash` or `gemini-1.5-pro` — `gemini-1.0-pro` doesn't have function calling.

## Privacy note

Calls to Gemini Flash on the free tier are **used by Google to improve their models** by default. If your demo data is sensitive, this matters.

For purely synthetic 5G simulator data (which is what this project generates), this is fine. For real production telemetry — don't use the free tier; pay for the API and check the data retention settings.

## Comparison: Gemini vs Claude Haiku

If you find Gemini Flash's quality insufficient for your demo, switching to Claude Haiku gives noticeably better results for ~$0.50-2/month. The setup is identical — different env vars:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-haiku-4-5
```

Compare on real demos:
- **Tool calling reliability**: Gemini ~85%, Haiku ~95%
- **Diagnosis quality**: Gemini decent, Haiku noticeably better
- **Speed**: Gemini ~3s/turn, Haiku ~2s/turn
- **Cost**: Gemini $0, Haiku ~$0.005/run

For most portfolio purposes, Gemini Flash is the right call. If you have one specific high-stakes demo where quality matters, swap to Haiku for that.
