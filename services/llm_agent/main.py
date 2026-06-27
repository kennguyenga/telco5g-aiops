"""
LLM Agent — supports three providers, swappable via env var LLM_PROVIDER:

  - "gemini"     (default) — Google Gemini Flash, FREE tier (15 req/min, 1M tokens/day)
  - "ollama"     — local model, no API key, but needs 8 GB RAM (CPX31+)
  - "anthropic"  — Claude Haiku/Sonnet via API, costs $$ but best quality

Two modes within each provider:
  1. CLASSIFIER (POST /api/llm/diagnose) — single-shot diagnosis from telemetry
  2. AGENT     (POST /api/llm/remediate) — tool-using loop

Tools available to the agent (9 total):
  - read_logs, query_metrics, get_topology, list_failures, clear_failure
  - query_error_codes, query_subscriber_states, reset_subscribers, classify_failure

Provider notes:
  - Gemini Flash has a TRUE free tier — no credit card, no expiring credits.
    Get a key at https://aistudio.google.com/apikey (sign in with Google).
    Limits: 15 RPM / 1500 RPD / 1M TPM — plenty for a portfolio demo.
    Quality: ~70-80% of Claude Sonnet for this use case.
  - Ollama runs Llama 3.1 8B locally. Free but slow on CPU (~30s/turn) and
    requires ~6 GB RAM available.
  - Anthropic Haiku is cheapest paid option (~$0.005/run). Best quality.
"""
import json
import os
import sys
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import traceback

# ── Provider config ──────────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower().strip()

# Google Gemini (default — free tier)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Ollama (local, free)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "180"))

# Anthropic (kept for opt-in fallback)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

# Service URLs
COLLECTOR_URL = os.getenv("COLLECTOR_URL", "http://collector:9000")
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:9001")

# Mock provider — needs NO API key. Runs a deterministic SRE playbook against
# the live telemetry (investigate → classify → remediate → verify) using the
# same 9 tools as the real providers, so the stack demos end-to-end without a
# token. Activated by LLM_PROVIDER=mock, or automatically when the selected
# provider has no credentials (set LLM_FALLBACK_MOCK=0 to restore hard 503s).
LLM_FALLBACK_MOCK = os.getenv("LLM_FALLBACK_MOCK", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def _provider_has_credentials(provider: str) -> bool:
    if provider == "gemini":
        return bool(GEMINI_API_KEY)
    if provider == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    # ollama needs no key (reachability handled in its loop); mock needs nothing
    return provider in ("ollama", "mock")


def _effective_provider() -> str:
    """The provider actually used for a request, after mock-fallback logic."""
    if LLM_PROVIDER == "mock":
        return "mock"
    if not _provider_has_credentials(LLM_PROVIDER) and LLM_FALLBACK_MOCK:
        return "mock"
    return LLM_PROVIDER


app = FastAPI(title="5G AIOps LLM Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Turn any unhandled exception into a visible, logged JSON error.

    Without this, an unexpected error surfaces as an opaque
    'Internal Server Error' in the UI with nothing in the logs to act on.
    Now the full traceback prints to `docker compose logs llm_agent`, and the
    dashboard receives the actual exception type and message.
    """
    tb = traceback.format_exc()
    print(f"[llm_agent] UNHANDLED on {request.method} {request.url.path}\n{tb}", flush=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc) or exc.__class__.__name__,
            "type": exc.__class__.__name__,
            "path": str(request.url.path),
            "hint": "See `docker compose logs llm_agent` for the full traceback.",
        },
    )


def _active_model() -> str:
    """Return the model name for the provider actually in effect."""
    eff = _effective_provider()
    if eff == "mock":
        return "mock-sre-playbook"
    if eff == "gemini":
        return GEMINI_MODEL
    if eff == "ollama":
        return OLLAMA_MODEL
    return CLAUDE_MODEL


@app.get("/healthz")
@app.get("/api/llm/healthz")
async def health():
    """Returns provider info + reachability."""
    eff = _effective_provider()
    if eff == "mock":
        return {
            "provider": "mock",
            "configured_provider": LLM_PROVIDER,
            "fallback": LLM_PROVIDER != "mock",
            "model": "mock-sre-playbook",
            "api_key_configured": False,
            "status": "ok",
            "note": "Deterministic SRE playbook — no API key required.",
        }
    info = {"provider": LLM_PROVIDER, "status": "ok"}
    if LLM_PROVIDER == "gemini":
        info["model"] = GEMINI_MODEL
        info["api_key_configured"] = bool(GEMINI_API_KEY)
        if GEMINI_API_KEY:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    r = await c.get(f"{GEMINI_BASE}/models?key={GEMINI_API_KEY}")
                    info["gemini_reachable"] = r.status_code == 200
                    if r.status_code != 200:
                        info["gemini_error"] = r.text[:200]
            except Exception as e:
                info["gemini_reachable"] = False
                info["gemini_error"] = str(e)[:200]
        return info
    if LLM_PROVIDER == "ollama":
        info["model"] = OLLAMA_MODEL
        info["ollama_url"] = OLLAMA_URL
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{OLLAMA_URL}/api/tags")
                if r.status_code == 200:
                    tags = [m["name"] for m in r.json().get("models", [])]
                    info["ollama_reachable"] = True
                    info["models_loaded"] = tags
                    info["model_loaded"] = OLLAMA_MODEL in tags
        except Exception as e:
            info["ollama_reachable"] = False
            info["ollama_error"] = str(e)[:200]
    else:
        info["model"] = CLAUDE_MODEL
        info["api_key_configured"] = bool(ANTHROPIC_API_KEY)
    return info


# ============================================================================
# CLASSIFIER MODE — single-shot diagnosis
# ============================================================================
SYSTEM_PROMPT_CLASSIFIER = """You are a senior SRE for a 5G mobile core. Analyze the telemetry and identify the root cause.

5G registration flow:
  UE → AMF → AUSF → UDM (auth vector) → AUSF → UDM (profile) → REGISTERED

PDU session establishment (post-registration):
  UE → AMF → SMF → PCF (policy) → UPF (install bearer) → ACTIVE

Common failure modes:
  - NF crash / blackhole → NF unreachable
  - NF slowdown → extra latency, p99 spikes
  - NF error_rate → 500s probabilistically
  - Coded 5G errors (TS 29.500): AUTH_REJECTED, USER_NOT_FOUND, ROAMING_NOT_ALLOWED,
    UE_AUTH_KEY_REVOKED, DNN_NOT_SUPPORTED, INSUFFICIENT_RESOURCES, NF_CONGESTION...
  - Subscriber state issues: BLOCKED, ROAMING_NOT_ALLOWED, AUTH_KEY_REVOKED,
    SUSPENDED → UDM emits matching 5G code on lookup

Output STRICT JSON only (no markdown, no explanation outside JSON):
{
  "root_cause": "<one sentence>",
  "affected_nf": "<nf name or 'multiple'>",
  "severity": "low|medium|high|critical",
  "evidence": ["<bullet>", "<bullet>"],
  "recommended_actions": ["<action>", "<action>"]
}"""


class DiagnoseRequest(BaseModel):
    nf: Optional[str] = None
    window_seconds: int = 300


@app.post("/api/llm/diagnose")
async def diagnose(req: DiagnoseRequest = None):
    """Single-shot LLM diagnosis from current telemetry."""
    req = req or DiagnoseRequest()

    # Pull telemetry context
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            summary = (await c.get(f"{COLLECTOR_URL}/api/summary")).json()
            failures = (await c.get(f"{ORCHESTRATOR_URL}/api/failures/state")).json()
        except Exception as e:
            raise HTTPException(503, f"telemetry unavailable: {e}")

    # Mock provider: derive the diagnosis directly from telemetry (no LLM call).
    if _effective_provider() == "mock":
        return {
            "diagnosis": _mock_diagnose(summary, failures),
            "provider": "mock",
            "configured_provider": LLM_PROVIDER,
            "model": "mock-sre-playbook",
        }

    # Compact telemetry for the prompt
    nfs_summary = {}
    for nf, data in (summary.get("nfs") or {}).items():
        if not isinstance(data, dict):
            continue
        # Pull just the metrics that matter
        compact = {}
        for k in ("requests_total", "requests_failed_total", "registrations_failed_total",
                   "requests_failed_injected_total", "p99_latency_ms"):
            if k in data:
                compact[k] = data[k]
        # Add error code counts
        for k, v in data.items():
            if isinstance(k, str) and k.startswith("errors_by_code_total"):
                compact[k] = v
        if compact:
            nfs_summary[nf] = compact

    user_msg = json.dumps({
        "active_failures": failures.get("nfs", {}),
        "nf_metrics": nfs_summary,
    }, indent=2)[:6000]

    response_text = await _call_llm(
        system=SYSTEM_PROMPT_CLASSIFIER,
        messages=[{"role": "user", "content": user_msg}],
        max_tokens=800,
    )

    # Try to parse JSON out of the response
    try:
        # Strip markdown fences if present
        clean = response_text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        diagnosis = json.loads(clean.strip())
    except Exception:
        diagnosis = {
            "root_cause": "Could not parse LLM output",
            "raw_response": response_text[:500],
        }

    return {
        "diagnosis": diagnosis,
        "provider": LLM_PROVIDER,
        "model": _active_model(),
    }


# ============================================================================
# AGENT MODE — tool-using loop
# ============================================================================
SYSTEM_PROMPT_AGENT = """You are an SRE agent for a 5G core. Use the tools to investigate and fix issues.

Process:
1. read_logs / query_metrics / query_error_codes — investigate what's wrong
2. query_subscriber_states — check if subscribers are blocked
3. classify_failure — get ML's diagnosis of the pattern
4. Use list_failures to see active fault injections
5. clear_failure(nf) or reset_subscribers() to remediate
6. Verify recovery with query_error_codes / get_topology
7. Stop when fixed or you've exhausted remediations

Be decisive: prefer 1-2 tools per step, don't loop on the same query.
When done, write a brief final summary (no tool call).

Common error codes you'll see:
- AUTH_REJECTED, UE_AUTH_KEY_REVOKED → likely subscriber state issue → reset_subscribers
- INSUFFICIENT_RESOURCES, NF_CONGESTION on UPF → clear_failure(upf)
- DNN_NOT_SUPPORTED on PCF → clear_failure(pcf)
- Many subscribers in non-ACTIVE state → reset_subscribers"""


# ── Tool definitions (Anthropic format — converted to Ollama format below) ──
TOOLS = [
    {"name": "read_logs",
     "description": "Read recent log entries from a specific NF (or all). Filter by level/since.",
     "input_schema": {
         "type": "object",
         "properties": {
             "nf": {"type": "string"},
             "level": {"type": "string", "enum": ["info", "warn", "error"]},
             "since_seconds": {"type": "integer", "default": 300},
         }}},
    {"name": "query_metrics",
     "description": "Get current metric values for an NF (request counts, error counts, latency).",
     "input_schema": {
         "type": "object",
         "properties": {"nf": {"type": "string"}},
         "required": ["nf"]}},
    {"name": "get_topology",
     "description": "Health and stats for all NFs in the 5G core.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_failures",
     "description": "What failures are currently injected on each NF.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "clear_failure",
     "description": "Clear injected failures on an NF. Remediation. nf='all' to clear everything.",
     "input_schema": {
         "type": "object",
         "properties": {"nf": {"type": "string", "enum": ["amf", "smf", "ausf", "udm", "nrf", "upf", "pcf", "all"]}},
         "required": ["nf"]}},
    {"name": "query_error_codes",
     "description": "Per-NF breakdown of 5G error codes (AUTH_REJECTED, USER_NOT_FOUND, etc.) emitted recently.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "query_subscriber_states",
     "description": "How many subscribers are in each state (ACTIVE, BLOCKED, AUTH_KEY_REVOKED, etc.).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "reset_subscribers",
     "description": "Reset all subscribers to ACTIVE state. Remediation when subscriber-level errors detected.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "classify_failure",
     "description": "ML pattern classifier — matches current error distribution against known scenarios.",
     "input_schema": {"type": "object", "properties": {}}},
]


async def _execute_tool(name: str, args: dict, client: httpx.AsyncClient) -> dict:
    """Execute a tool call and return its result."""
    try:
        if name == "read_logs":
            params = {"limit": 50}
            for k in ("nf", "level", "since_seconds"):
                if args.get(k) is not None:
                    params[k] = args[k]
            r = await client.get(f"{COLLECTOR_URL}/api/logs", params=params, timeout=10)
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}

        if name == "query_metrics":
            r = await client.get(f"{COLLECTOR_URL}/api/summary", timeout=10)
            summary = r.json()
            nf = args.get("nf")
            return summary.get("nfs", {}).get(nf, {"error": f"no data for {nf}"}) if nf else summary

        if name == "get_topology":
            r = await client.get(f"{ORCHESTRATOR_URL}/api/topology", timeout=10)
            return r.json()

        if name == "list_failures":
            r = await client.get(f"{ORCHESTRATOR_URL}/api/failures/state", timeout=10)
            return r.json()

        if name == "clear_failure":
            nf = args["nf"]
            params = {} if nf == "all" else {"nf": nf}
            r = await client.post(f"{ORCHESTRATOR_URL}/api/failures/clear", params=params, timeout=10)
            return r.json()

        if name == "query_error_codes":
            r = await client.get(f"{COLLECTOR_URL}/api/summary", timeout=10)
            summary = r.json()
            out = {}
            for nf, data in (summary.get("nfs") or {}).items():
                if not isinstance(data, dict):
                    continue
                codes = {}
                for k, v in data.items():
                    if isinstance(k, str) and k.startswith("errors_by_code_total") and "code=" in k:
                        code = k.split("code=", 1)[1].rstrip("}").strip()
                        codes[code] = codes.get(code, 0) + (v or 0)
                if codes:
                    out[nf] = codes
            return {"error_codes_by_nf": out, "total": sum(c for nf in out.values() for c in nf.values())}

        if name == "query_subscriber_states":
            udm_url = os.getenv("UDM_URL", "http://udm:8003")
            r = await client.get(f"{udm_url}/subscribers/state/summary", timeout=10)
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}

        if name == "reset_subscribers":
            udm_url = os.getenv("UDM_URL", "http://udm:8003")
            r = await client.post(f"{udm_url}/subscribers/state/reset", timeout=10)
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}

        if name == "classify_failure":
            ml_url = os.getenv("ML_ENGINE_URL", "http://ml_engine:9002")
            r = await client.post(f"{ml_url}/api/ml/classify-failure", timeout=20)
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}

        return {"error": f"unknown tool: {name}"}
    except Exception as e:
        return {"error": str(e), "tool": name}


class RemediateRequest(BaseModel):
    user_goal: Optional[str] = "Investigate and fix any active issues."
    max_iterations: int = 6


@app.post("/api/llm/remediate")
async def remediate(req: RemediateRequest):
    """Tool-using agent loop. Provider-agnostic — works with Mock, Gemini, Ollama, or Claude."""
    transcript = []
    eff = _effective_provider()

    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT + 30) as client:
        if eff == "mock":
            await _agent_loop_mock(req, transcript, client)
        elif eff == "gemini":
            await _agent_loop_gemini(req, transcript, client)
        elif eff == "ollama":
            await _agent_loop_ollama(req, transcript, client)
        else:
            await _agent_loop_anthropic(req, transcript, client)

    return {
        "transcript": transcript,
        "iterations": len(transcript),
        "final_message": transcript[-1] if transcript else None,
        "provider": eff,
        "configured_provider": LLM_PROVIDER,
        "model": _active_model(),
    }


# ============================================================================
# AGENT LOOP — GEMINI (Google Gemini Flash with function calling)
# ============================================================================
def _tools_to_gemini(tools: list) -> list:
    """Convert Anthropic tool format → Gemini function-calling format."""
    out = []
    for t in tools:
        schema = dict(t["input_schema"])
        # Gemini doesn't accept empty objects with no properties
        if not schema.get("properties"):
            schema = {"type": "object", "properties": {"_unused": {"type": "string"}}}
        out.append({
            "name": t["name"],
            "description": t["description"],
            "parameters": schema,
        })
    return [{"functionDeclarations": out}]


async def _agent_loop_gemini(req: RemediateRequest, transcript: list,
                              client: httpx.AsyncClient):
    """Agent loop using Gemini's generateContent with function calling.

    Gemini's tool-use is structured like OpenAI's: each model turn has
    parts that are either text or functionCall. Tool results come back
    as functionResponse parts.
    """
    gemini_tools = _tools_to_gemini(TOOLS)
    contents = [{"role": "user", "parts": [{"text": req.user_goal}]}]
    system_inst = {"parts": [{"text": SYSTEM_PROMPT_AGENT}]}

    for iteration in range(req.max_iterations):
        try:
            r = await client.post(
                f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
                json={
                    "systemInstruction": system_inst,
                    "contents": contents,
                    "tools": gemini_tools,
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": 800},
                },
                timeout=60,
            )
        except Exception as e:
            transcript.append({"iteration": iteration,
                               "error": f"gemini unreachable: {e}",
                               "stop_reason": "error"})
            return

        if r.status_code != 200:
            transcript.append({"iteration": iteration,
                               "error": f"gemini HTTP {r.status_code}: {r.text[:300]}",
                               "stop_reason": "error"})
            return

        body = r.json()
        candidates = body.get("candidates", [])
        if not candidates:
            transcript.append({"iteration": iteration,
                               "error": "gemini returned no candidates",
                               "raw": str(body)[:300],
                               "stop_reason": "error"})
            return

        cand_content = candidates[0].get("content", {})
        parts = cand_content.get("parts", [])

        # Build transcript turn in Anthropic shape so frontend renders it identically
        turn_content = []
        function_calls = []
        for p in parts:
            if "text" in p:
                turn_content.append({"type": "text", "text": p["text"]})
            elif "functionCall" in p:
                fc = p["functionCall"]
                turn_content.append({
                    "type": "tool_use",
                    "id": f"call_{iteration}_{len(function_calls)}",
                    "name": fc.get("name", ""),
                    "input": fc.get("args", {}) or {},
                })
                function_calls.append(fc)

        transcript.append({
            "iteration": iteration,
            "stop_reason": "tool_use" if function_calls else "end_turn",
            "content": turn_content,
        })

        if not function_calls:
            return

        # Append model turn so Gemini sees its own function calls in history
        contents.append({"role": "model", "parts": parts})

        # Execute tools, return results as functionResponse parts
        response_parts = []
        for fc in function_calls:
            name = fc.get("name", "")
            args = (fc.get("args", {}) or {}).copy()
            args.pop("_unused", None)

            result = await _execute_tool(name, args, client)
            response_parts.append({
                "functionResponse": {
                    "name": name,
                    "response": {"result": json.dumps(result)[:3000]},
                },
            })
            transcript[-1].setdefault("tool_results", []).append({
                "tool": name,
                "input": args,
                "result_preview": str(result)[:500],
            })

        contents.append({"role": "user", "parts": response_parts})

    # Max iterations — ask for synthesis
    contents.append({
        "role": "user",
        "parts": [{"text": "You've reached the iteration limit. Briefly summarize what you found and did."}],
    })
    try:
        r = await client.post(
            f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            json={"systemInstruction": system_inst, "contents": contents,
                  "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400}},
            timeout=60,
        )
        if r.status_code == 200:
            body = r.json()
            text = ""
            try:
                text = body["candidates"][0]["content"]["parts"][0].get("text", "")
            except (KeyError, IndexError):
                pass
            transcript.append({
                "iteration": req.max_iterations,
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": text}],
            })
    except Exception:
        pass


# ============================================================================
# AGENT LOOP — OLLAMA
# ============================================================================
def _tools_to_ollama(tools: list) -> list:
    """Convert Anthropic tool format → Ollama (OpenAI-compatible) tool format."""
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return out


async def _agent_loop_ollama(req: RemediateRequest, transcript: list,
                              client: httpx.AsyncClient):
    """Agent loop using Ollama's OpenAI-compatible /api/chat with tools.

    Llama 3.1 8B has acceptable but unreliable tool-use. This loop is more
    defensive than the Claude version: it bails out cleanly on malformed
    tool calls rather than crashing.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_AGENT},
        {"role": "user", "content": req.user_goal},
    ]
    ollama_tools = _tools_to_ollama(TOOLS)

    for iteration in range(req.max_iterations):
        try:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "tools": ollama_tools,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 800},
                },
                timeout=OLLAMA_TIMEOUT,
            )
        except Exception as e:
            transcript.append({
                "iteration": iteration,
                "error": f"ollama unreachable: {e}",
                "stop_reason": "error",
            })
            return

        if r.status_code != 200:
            transcript.append({
                "iteration": iteration,
                "error": f"ollama HTTP {r.status_code}: {r.text[:300]}",
                "stop_reason": "error",
            })
            return

        body = r.json()
        msg = body.get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        # Build transcript turn in the same shape the frontend expects
        turn_content = []
        if content:
            turn_content.append({"type": "text", "text": content})
        for tc in tool_calls:
            fn = tc.get("function", {})
            turn_content.append({
                "type": "tool_use",
                "id": tc.get("id", f"call_{iteration}_{len(turn_content)}"),
                "name": fn.get("name", ""),
                "input": fn.get("arguments", {}) if isinstance(fn.get("arguments"), dict)
                         else _safe_json_load(fn.get("arguments", "{}")),
            })
        transcript.append({
            "iteration": iteration,
            "stop_reason": "tool_use" if tool_calls else "end_turn",
            "content": turn_content,
        })

        # No tool calls → final answer; stop
        if not tool_calls:
            return

        # Append assistant turn for next round
        messages.append(msg)

        # Execute every tool call, append results
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            args = raw_args if isinstance(raw_args, dict) else _safe_json_load(raw_args)

            result = await _execute_tool(name, args, client)
            messages.append({
                "role": "tool",
                "content": json.dumps(result)[:3000],  # truncate huge results
            })
            transcript[-1].setdefault("tool_results", []).append({
                "tool": name,
                "input": args,
                "result_preview": str(result)[:500],
            })

    # Hit max iterations — add a synthesis prompt so user sees a wrap-up
    messages.append({
        "role": "user",
        "content": "You've reached the iteration limit. Briefly summarize what you found and did.",
    })
    try:
        r = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": False,
                  "options": {"temperature": 0.3, "num_predict": 400}},
            timeout=OLLAMA_TIMEOUT,
        )
        if r.status_code == 200:
            transcript.append({
                "iteration": req.max_iterations,
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": r.json().get("message", {}).get("content", "")}],
            })
    except Exception:
        pass


def _safe_json_load(s: str) -> dict:
    """Parse JSON-or-similar string from a Llama tool call. Returns {} on failure."""
    if not s:
        return {}
    try:
        return json.loads(s) if isinstance(s, str) else s
    except Exception:
        return {}


# ============================================================================
# AGENT LOOP — ANTHROPIC (kept as opt-in via LLM_PROVIDER=anthropic)
# ============================================================================
async def _agent_loop_anthropic(req: RemediateRequest, transcript: list,
                                 client: httpx.AsyncClient):
    """Original Claude-based agent loop. Higher quality, costs API tokens."""
    messages = [{"role": "user", "content": req.user_goal}]

    for iteration in range(req.max_iterations):
        try:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 1500,
                    "system": SYSTEM_PROMPT_AGENT,
                    "messages": messages,
                    "tools": TOOLS,
                },
                timeout=60,
            )
        except Exception as e:
            transcript.append({"iteration": iteration, "error": f"claude unreachable: {e}",
                               "stop_reason": "error"})
            return

        if r.status_code != 200:
            transcript.append({"iteration": iteration,
                               "error": f"claude HTTP {r.status_code}: {r.text[:300]}",
                               "stop_reason": "error"})
            return

        response = r.json()
        transcript.append({
            "iteration": iteration,
            "stop_reason": response.get("stop_reason"),
            "content": response.get("content", []),
        })
        messages.append({"role": "assistant", "content": response["content"]})

        if response.get("stop_reason") != "tool_use":
            return

        tool_results = []
        for block in response["content"]:
            if block["type"] == "tool_use":
                result = await _execute_tool(block["name"], block["input"], client)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(result)[:4000],
                })
                transcript[-1].setdefault("tool_results", []).append({
                    "tool": block["name"],
                    "input": block["input"],
                    "result_preview": str(result)[:500],
                })
        messages.append({"role": "user", "content": tool_results})


# ============================================================================
# UNIFIED LLM CALL (used by classifier mode)
# ============================================================================
async def _call_llm(system: str, messages: list, max_tokens: int = 800) -> str:
    """Provider-agnostic single-call LLM. Returns the text response."""
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT + 30) as client:
        if LLM_PROVIDER == "gemini":
            if not GEMINI_API_KEY:
                raise HTTPException(503, "GEMINI_API_KEY not configured")
            # Build Gemini-style request — system goes in systemInstruction;
            # messages in contents[] with role="user"/"model".
            contents = []
            for m in messages:
                role = "user" if m["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
            payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": max_tokens,
                },
            }
            r = await client.post(
                f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
                json=payload,
                timeout=60,
            )
            if r.status_code != 200:
                raise HTTPException(502, f"gemini error {r.status_code}: {r.text[:300]}")
            body = r.json()
            try:
                return body["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                return json.dumps(body)[:500]
        if LLM_PROVIDER == "ollama":
            full_messages = [{"role": "system", "content": system}] + messages
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": full_messages,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": max_tokens},
                },
                timeout=OLLAMA_TIMEOUT,
            )
            if r.status_code != 200:
                raise HTTPException(502, f"ollama error {r.status_code}: {r.text[:300]}")
            return r.json().get("message", {}).get("content", "")
        else:
            if not ANTHROPIC_API_KEY:
                raise HTTPException(503, "ANTHROPIC_API_KEY not configured")
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": messages,
                },
                timeout=60,
            )
            if r.status_code != 200:
                raise HTTPException(502, f"claude error {r.status_code}: {r.text[:300]}")
            content = r.json().get("content", [])
            return "".join(b.get("text", "") for b in content if b.get("type") == "text")


# ============================================================================
# MOCK PROVIDER — deterministic SRE playbook (no API key required)
# ============================================================================
# Runs the same investigate→classify→remediate→verify flow a real agent would,
# but with rule-based decisions instead of an LLM. It calls the real tools, so
# it genuinely fixes injected faults and produces an Anthropic-shaped transcript
# the frontend renders identically to a live provider.

# 5G causes that point at subscriber state rather than an NF-level fault.
SUBSCRIBER_CODES = {
    "AUTH_REJECTED", "UE_AUTH_KEY_REVOKED", "ROAMING_NOT_ALLOWED",
    "USER_NOT_ALLOWED", "USER_NOT_FOUND", "ILLEGAL_UE",
    "SUBSCRIPTION_NOT_FOUND", "PLMN_NOT_ALLOWED",
}


def _nf_has_fault(cfg: dict) -> bool:
    """True if a /failure config dict represents any active injection."""
    return bool(
        cfg.get("blackhole")
        or cfg.get("unhealthy")
        or (cfg.get("error_rate") or 0) > 0
        or (cfg.get("extra_latency_ms") or 0) > 0
        or (cfg.get("error_code_rate") or 0) > 0
        or cfg.get("error_codes")
    )


def _aggregate_error_codes(summary: dict) -> dict:
    """Flatten errors_by_code_total{code=X} counters across all NFs."""
    out: dict = {}
    for _nf, data in (summary.get("nfs") or {}).items():
        if not isinstance(data, dict):
            continue
        for k, v in data.items():
            if isinstance(k, str) and k.startswith("errors_by_code_total") and "code=" in k:
                code = k.split("code=", 1)[1].rstrip("}").strip()
                out[code] = out.get(code, 0) + int(v or 0)
    return out


def _faulted_nfs(failures: dict) -> dict:
    return {
        nf: cfg
        for nf, cfg in (failures.get("nfs") or {}).items()
        if isinstance(cfg, dict) and _nf_has_fault(cfg)
    }


def _describe_findings(agg: dict, faulted: dict) -> str:
    parts = []
    if agg:
        top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:3]
        parts.append("Seeing " + ", ".join(f"{v}× {c}" for c, v in top))
    if faulted:
        parts.append("injected faults on " + ", ".join(faulted))
    return (". ".join(parts) + "." if parts else "No strong signal in the telemetry.")


def _mock_diagnose(summary: dict, failures: dict) -> dict:
    """Single-shot classifier output, derived from telemetry without an LLM."""
    agg = _aggregate_error_codes(summary)
    faulted = _faulted_nfs(failures)
    if not agg and not faulted:
        return {
            "root_cause": "No active faults or 5G error codes observed; core is healthy.",
            "affected_nf": "none",
            "severity": "low",
            "evidence": ["No injected failures", "No 5G error codes in the window"],
            "recommended_actions": ["No action required"],
        }
    top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
    dominant = top[0][0] if top else None
    sub_total = sum(v for c, v in agg.items() if c in SUBSCRIBER_CODES)
    has_blackhole = any(cfg.get("blackhole") for cfg in faulted.values())
    severity = (
        "critical" if has_blackhole
        else "high" if (faulted or sum(agg.values()) > 20)
        else "medium"
    )
    if dominant in SUBSCRIBER_CODES:
        root = f"Subscriber-state errors ({dominant}) — UDM is rejecting affected SUPIs on lookup."
    elif has_blackhole:
        nf = next((nf for nf, cfg in faulted.items() if cfg.get("blackhole")), "an NF")
        root = f"{nf} is black-holing requests, stalling the call flow upstream."
    elif faulted:
        root = "Injected fault(s) on " + ", ".join(faulted) + " are degrading the core."
    elif dominant:
        root = f"Elevated {dominant} across the core without a recorded injection."
    else:
        root = "Degradation detected."
    affected = list(faulted.keys())
    actions = []
    if sub_total > 0:
        actions.append("reset_subscribers (subscriber-state errors present)")
    actions += [f"clear_failure({nf})" for nf in faulted]
    if not actions:
        actions.append("clear_failure(all) then continue monitoring")
    evidence = []
    if faulted:
        evidence.append("Injected faults on: " + ", ".join(faulted))
    evidence += [f"{v}× {c}" for c, v in top[:4]]
    return {
        "root_cause": root,
        "affected_nf": (affected[0] if len(affected) == 1 else "multiple") if affected else "multiple",
        "severity": severity,
        "evidence": evidence,
        "recommended_actions": actions,
    }


def _mock_iter(i: int, text: str, tool_calls: list, stop_reason: str) -> dict:
    """Build one Anthropic-shaped transcript turn."""
    content = [{"type": "text", "text": text}]
    for n, (name, inp) in enumerate(tool_calls):
        content.append({"type": "tool_use", "id": f"mock_{i}_{n}", "name": name, "input": inp})
    return {"iteration": i, "stop_reason": stop_reason, "content": content}


async def _mock_run(calls: list, client) -> list:
    """Execute (name, input) tool calls, returning (name, input, result) triples."""
    out = []
    for name, inp in calls:
        out.append((name, inp, await _execute_tool(name, inp, client)))
    return out


def _attach_results(entry: dict, ran: list) -> dict:
    entry["tool_results"] = [
        {"tool": n, "input": ip, "result_preview": str(r)[:500]} for n, ip, r in ran
    ]
    return entry


async def _agent_loop_mock(req: "RemediateRequest", transcript: list, client: httpx.AsyncClient):
    i = 0

    # ── 1. Investigate ────────────────────────────────────────────────
    invest = [("get_topology", {}), ("query_error_codes", {}), ("list_failures", {})]
    ran = await _mock_run(invest, client)
    transcript.append(_attach_results(
        _mock_iter(i, "Starting investigation: pulling topology, recent 5G error "
                      "codes, and the active fault-injection state.", invest, "tool_use"), ran))
    i += 1

    codes_res = ran[1][2] if isinstance(ran[1][2], dict) else {}
    fail_res = ran[2][2] if isinstance(ran[2][2], dict) else {}
    agg: dict = {}
    for _nf, cmap in (codes_res.get("error_codes_by_nf") or {}).items():
        if isinstance(cmap, dict):
            for c, v in cmap.items():
                agg[c] = agg.get(c, 0) + int(v or 0)
    faulted = _faulted_nfs(fail_res)
    sub_hits = {c: v for c, v in agg.items() if c in SUBSCRIBER_CODES}

    # Healthy → stop early.
    if not agg and not faulted:
        transcript.append({"iteration": i, "stop_reason": "end_turn", "content": [{
            "type": "text",
            "text": "No active fault injections and no 5G error codes in the window. "
                    "The core is healthy; no remediation needed."}]})
        return

    # ── 2. Deepen: ML classify + subscriber states ────────────────────
    deepen = [("classify_failure", {}), ("query_subscriber_states", {})]
    ran = await _mock_run(deepen, client)
    transcript.append(_attach_results(
        _mock_iter(i, _describe_findings(agg, faulted)
                   + " Asking the ML classifier to match the pattern and checking "
                     "subscriber states.", deepen, "tool_use"), ran))
    i += 1

    # ── 3. Remediate ──────────────────────────────────────────────────
    remediate_calls = []
    if sub_hits:
        remediate_calls.append(("reset_subscribers", {}))
    if faulted:
        names = list(faulted)
        if len(names) >= 3:
            remediate_calls.append(("clear_failure", {"nf": "all"}))
        else:
            remediate_calls += [("clear_failure", {"nf": nf}) for nf in names]
    if not remediate_calls:  # codes but no recorded injection — safe blanket clear
        remediate_calls.append(("clear_failure", {"nf": "all"}))

    plan = []
    if sub_hits:
        plan.append("resetting subscribers (" + ", ".join(f"{v}×{c}" for c, v in sub_hits.items()) + ")")
    if faulted:
        plan.append("clearing injected faults on " + ", ".join(faulted))
    if not plan:
        plan.append("clearing all fault injections as a safe default")
    ran = await _mock_run(remediate_calls, client)
    transcript.append(_attach_results(
        _mock_iter(i, "Remediating: " + "; ".join(plan) + ".", remediate_calls, "tool_use"), ran))
    i += 1

    # ── 4. Verify ─────────────────────────────────────────────────────
    verify = [("query_error_codes", {}), ("list_failures", {})]
    ran = await _mock_run(verify, client)
    transcript.append(_attach_results(
        _mock_iter(i, "Verifying recovery: re-reading 5G error codes and fault state.",
                   verify, "tool_use"), ran))
    i += 1

    # ── 5. Summary ────────────────────────────────────────────────────
    after = ran[1][2] if isinstance(ran[1][2], dict) else {}
    still = list(_faulted_nfs(after))
    if still:
        summary = ("Cleared the primary faults. " + ", ".join(still)
                   + " still shows injected config — a second pass may be needed.")
    else:
        summary = ("All injected faults cleared"
                   + (" and subscribers reset" if sub_hits else "")
                   + ". Fault state is clean; the core should recover as new "
                     "registrations succeed.")
    transcript.append({"iteration": i, "stop_reason": "end_turn",
                       "content": [{"type": "text", "text": summary}]})
