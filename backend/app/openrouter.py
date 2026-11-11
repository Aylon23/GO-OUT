"""OpenRouter client: free models only, shared by the scenario debate and the stock council.

Rules every call follows:
- Free models only: while the setting is on (default), any model id in the chain that is not a
  ":free" variant is refused before anything is sent.
- Privacy: every request body passes `privacy.assert_clean` (no names, no amounts).
- Each call sends one primary model plus up to two fallbacks in OpenRouter's "models" array.
- Pacing: at most PACE_PER_MIN requests in any rolling minute (OpenRouter allows 20 for free models).
- At most one retry per request; a failed request still counts against the daily quota.
- Answers must be JSON matching a schema. Invalid JSON gets one repair request, then the call fails.
- Every call is logged in AiCall: model, role, tokens, latency, requests sent, payload hash.
- The daily free-request count comes from GET /api/v1/key; requests sent since that read are added.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from sqlmodel import Session, select

from . import privacy
from .db import ROOT
from .models import AiCall, AiQuota

BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1").rstrip("/")  # override only for local testing
PACE_PER_MIN = 19  # stay under OpenRouter's 20 requests/minute for free models
TIMEOUT = 90.0
MAX_TOKENS = 4000
KEY_TTL = dt.timedelta(seconds=60)  # how long a /key read is trusted before reading again

DEFAULT_ROSTER: list[dict] = [
    {"role": "analyst_1", "label": "Analyst 1", "models": ["google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free", "recursive/flint-v1.0:free"]},
    {"role": "analyst_2", "label": "Analyst 2", "models": ["nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3.5-super-vl:free", "nvidia/nemotron-3.5-lightning:free"]},
    {"role": "analyst_3", "label": "Analyst 3", "models": ["thinkingmachines/inkling:free", "thinkingmachines/inkling-small:free", "xingchen-agi/xing4.0-29b-a4b:free"]},
    {"role": "analyst_4", "label": "Analyst 4", "models": ["dots-studio/dots-3-note-preview:free", "xingchen-agi/xing4.0-29b-a4b:free", "recursive/flint-v1.0:free"]},
    {"role": "judge", "label": "Judge", "models": ["nvidia/nemotron-3.5-super-vl:free", "google/gemma-4-31b-it:free", "thinkingmachines/inkling:free"]},
]
KNOWN_FREE = sorted({m for r in DEFAULT_ROSTER for m in r["models"]} | {
    "cohere/north-mini-code:free", "liquid/lfm-2.5-2.6b:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"})


class AiError(Exception):
    pass


class NoKey(AiError):
    pass


class FreeOnlyError(AiError):
    pass


class QuotaExhausted(AiError):
    pass


class InvalidAnswer(AiError):
    pass


# ---------- key ----------

def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():  # utf-8-sig: Notepad may add a byte-order mark
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def api_key() -> str:
    """OPENROUTER_API_KEY from the environment, or from .env next to the app."""
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k
    for p in (ROOT / ".env", ROOT / "backend" / ".env"):
        k = _read_env_file(p).get("OPENROUTER_API_KEY", "").strip()
        if k:
            return k
    return ""


# ---------- transport (replaced in tests) ----------

class HttpxTransport:
    def request(self, method: str, url: str, headers: dict, body: dict | None, timeout: float):
        import httpx

        r = httpx.request(method, url, headers=headers, json=body, timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            data = {"error": {"message": r.text[:300]}}
        return r.status_code, dict(r.headers), data


_transport = HttpxTransport()
_clock = time.monotonic
_sleep = time.sleep


def set_transport(t) -> None:
    global _transport
    _transport = t or HttpxTransport()


def set_clock(clock, sleep) -> None:
    global _clock, _sleep
    _clock, _sleep = clock or time.monotonic, sleep or time.sleep


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost", "X-Title": "Benchmark (local portfolio tracker)"}


# ---------- pacing ----------

_pace_lock = threading.Lock()
_sent: collections.deque = collections.deque()
_total_sent = 0  # requests sent since start; a call's count is the difference before and after it


def _pace() -> None:
    """Wait until sending one more request keeps us at PACE_PER_MIN in any rolling 60 seconds."""
    global _total_sent
    with _pace_lock:
        while True:
            now = _clock()
            while _sent and now - _sent[0] >= 60.0:
                _sent.popleft()
            if len(_sent) < PACE_PER_MIN:
                _sent.append(now)
                _total_sent += 1
                return
            _sleep(60.0 - (now - _sent[0]) + 0.01)


def reset_pacing() -> None:
    with _pace_lock:
        _sent.clear()


# ---------- settings ----------

def roster(session: Session) -> list[dict]:
    from .services import get_settings

    raw = (get_settings(session).ai_roster or "").strip()
    if raw:
        try:
            r = json.loads(raw)
            if isinstance(r, list) and r:
                return r
        except ValueError:
            pass
    return [dict(x, models=list(x["models"])) for x in DEFAULT_ROSTER]


def free_only(session: Session) -> bool:
    from .services import get_settings

    return bool(get_settings(session).ai_free_only)


def check_models(session: Session, models: list[str]) -> list[str]:
    models = [m.strip() for m in models if m and m.strip()]
    if not models:
        raise AiError("No model set for this role.")
    if len(models) > 3:
        raise AiError("One model plus at most two fallbacks.")
    if free_only(session):
        bad = [m for m in models if not m.endswith(":free")]
        if bad:
            raise FreeOnlyError(f"Free models only is on: {', '.join(bad)} is not a :free model.")
    return models


# ---------- quota ----------

def _utc_day(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).date().isoformat()


def _quota_row(session: Session) -> AiQuota:
    q = session.get(AiQuota, 1)
    if q is None:
        q = AiQuota(id=1)
        session.add(q)
        session.commit()
        session.refresh(q)
    return q


def refresh_quota(session: Session, force: bool = False) -> AiQuota | None:
    """Read free_model_daily_requests from GET /api/v1/key (not counted against the quota)."""
    key = api_key()
    if not key:
        return None
    q = _quota_row(session)
    now = dt.datetime.now(dt.timezone.utc)
    checked = q.checked_at.replace(tzinfo=dt.timezone.utc) if q.checked_at and q.checked_at.tzinfo is None else q.checked_at
    if not force and checked and q.day == _utc_day(now) and now - checked < KEY_TTL:
        return q
    status, _h, data = _transport.request("GET", f"{BASE}/key", _headers(key), None, 20.0)
    if status != 200:
        raise AiError(f"OpenRouter didn't accept the key ({status}).")
    f = (data.get("data") or {}).get("free_model_daily_requests") or {}
    q.day = _utc_day(now)
    q.limit, q.used, q.remaining = int(f.get("limit") or 0), int(f.get("used") or 0), int(f.get("remaining") or 0)
    q.sent_since = 0
    q.checked_at = now
    session.add(q)
    session.commit()
    return q


def quota_status(session: Session, refresh: bool = True) -> dict:
    """{"limit", "used", "remaining", "as_of", "known"}: the API's figures plus requests sent since."""
    key_set = bool(api_key())
    if key_set and refresh:
        try:
            refresh_quota(session)
        except Exception as exc:  # offline: show the last known figures
            err = str(exc)
        else:
            err = ""
    else:
        err = ""
    q = session.get(AiQuota, 1)
    if q is None or not q.day:
        return {"known": False, "key_set": key_set, "error": err}
    if q.day != _utc_day():  # a new UTC day: the counter has reset
        return {"known": True, "key_set": key_set, "limit": q.limit, "used": 0, "remaining": q.limit,
                "as_of": None, "error": err, "stale_day": True}
    used = q.used + q.sent_since
    return {"known": True, "key_set": key_set, "limit": q.limit, "used": used, "remaining": max(0, q.remaining - q.sent_since),
            "as_of": q.checked_at.isoformat() if q.checked_at else None, "error": err}


def _count_sent(session: Session, n: int = 1) -> None:
    q = _quota_row(session)
    if q.day == _utc_day():
        q.sent_since += n
    session.add(q)
    session.commit()


class BudgetReached(AiError):
    pass


def month_spent(session: Session, now: dt.datetime | None = None) -> float:
    """USD spent on paid models since the 1st of this month (UTC), from the call log."""
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = session.exec(select(AiCall).where(AiCall.paid == True, AiCall.at >= start)).all()  # noqa: E712
    return float(sum(r.cost or 0.0 for r in rows))


def budget_status(session: Session) -> dict:
    from .services import get_settings

    limit = float(get_settings(session).ai_budget_usd or 0.0)
    spent = month_spent(session)
    return {"limit": limit, "spent": round(spent, 4), "left": round(max(0.0, limit - spent), 4)}


def requests_left(session: Session) -> int | None:
    s = quota_status(session)
    return s.get("remaining") if s.get("known") else None


# ---------- JSON ----------

def payload_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _extract_json(text: str):
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith(("{", "[")):
        i = min([x for x in (t.find("{"), t.find("[")) if x >= 0], default=-1)
        if i >= 0:
            t = t[i:]
    return json.loads(t)


def _validate(obj, schema: dict) -> list[str]:
    import jsonschema

    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in v.iter_errors(obj)][:8]


# ---------- the call ----------

def _send(session: Session, body: dict, paid: bool = False, timeout: float = TIMEOUT) -> tuple[int, dict, dict]:
    """One request with at most one retry. Every attempt is paced and counted (free requests against the
    daily quota; paid ones are checked against the monthly budget by the caller)."""
    key = api_key()
    if not key:
        raise NoKey("Add OPENROUTER_API_KEY to the .env file next to the app, then restart it.")
    last: tuple[int, dict, dict] | None = None
    for attempt in range(2):
        if not paid:
            left = requests_left(session)
            if left is not None and left <= 0:
                raise QuotaExhausted("No free AI requests left today.")
        _pace()
        if not paid:
            _count_sent(session)
        try:
            status, headers, data = _transport.request("POST", f"{BASE}/chat/completions", _headers(key), body, timeout)
        except Exception as exc:  # network error or timeout
            status, headers, data = 0, {}, {"error": {"message": str(exc)[:300]}}
        last = (status, headers, data)
        if status == 200 and not data.get("error") and data.get("choices"):
            return last
        if status in (400, 401, 402, 403, 404):  # won't get better on retry
            break
        if attempt == 0 and status == 429:
            wait = headers.get("retry-after") or headers.get("Retry-After")
            try:
                _sleep(min(30.0, float(wait)))
            except (TypeError, ValueError):
                pass
    return last  # type: ignore[return-value]


def _err_text(status: int, data: dict) -> str:
    msg = ((data or {}).get("error") or {}).get("message") or ""
    return f"HTTP {status}: {msg}"[:300] if status else msg[:300] or "No answer"


def chat_json(session: Session, role: str, models: list[str], messages: list[dict], schema: dict,
              schema_name: str = "answer", max_tokens: int = MAX_TOKENS, job_id: int | None = None,
              step_key: str = "", paid: bool = False, web: bool = False, guidance: str | None = None,
              timeout: float = TIMEOUT) -> tuple[dict, AiCall]:
    """Ask the model chain for JSON matching `schema`. Returns (answer, logged call).

    paid=True is the analyst panel: any model id is allowed, the call is checked against the monthly
    budget instead of the free daily quota, and its cost is logged. web=True adds OpenRouter's web search.
    `guidance` replaces the default "rely only on the data given" line."""
    call = AiCall(role=role, models=json.dumps(models), job_id=job_id, step_key=step_key, paid=paid)
    t0 = _clock()
    try:
        if paid:
            models = [m.strip() for m in models if m and m.strip()][:3]
            if not models:
                raise AiError("No model set for this seat.")
            b = budget_status(session)
            if b["left"] <= 0:
                raise BudgetReached(f"The monthly AI budget (${b['limit']:.2f}) is used up. Raise it in Settings, or wait for the 1st.")
        else:
            models = check_models(session, models)
        sys_msg = {"role": "system", "content": (
            "Answer with one JSON object only, no prose and no code fences. It must match this JSON schema "
            f"({schema_name}):\n" + json.dumps(schema, separators=(",", ":")) + "\n" +
            (guidance or "Rely only on the data given in the messages, not on memory; write \"missing\" where the data lacks something."))}
        body = {"models": models, "messages": [sys_msg, *messages], "max_tokens": max_tokens, "temperature": 0.3}
        if paid:
            body["usage"] = {"include": True}  # OpenRouter then reports the cost of the call
        if web:
            body["plugins"] = [{"id": "web", "max_results": 5}]
        # what the model reads is checked; request settings (max_tokens and the like) are not content
        privacy.assert_clean(session, body["messages"])
        call.payload_hash = payload_hash(body)
    except (AiError, privacy.PrivacyError) as exc:
        call.status, call.error = "refused", str(exc)
        _log(session, call)
        raise
    answer = None
    sent_before = _sent_total(session)
    try:
        status, _h, data = _send(session, body, paid, timeout)
        _add_usage(call, data)
        if status != 200 or not data.get("choices"):
            raise AiError(_err_text(status, data))
        text = data["choices"][0].get("message", {}).get("content") or ""
        problems: list[str]
        try:
            answer = _extract_json(text)
            problems = _validate(answer, schema)
        except ValueError as exc:
            problems = [f"not valid JSON ({exc})"]
        if problems:  # one repair attempt
            fix = {**body, "messages": [*body["messages"], {"role": "assistant", "content": text[:6000]},
                                        {"role": "user", "content": "That answer failed validation: " + "; ".join(problems) +
                                         ". Return only the corrected JSON object."}]}
            privacy.assert_clean(session, fix["messages"])
            status, _h, data = _send(session, fix, paid, timeout)
            _add_usage(call, data)
            if status != 200 or not data.get("choices"):
                raise InvalidAnswer("Invalid JSON, and the repair request failed: " + _err_text(status, data))
            text = data["choices"][0].get("message", {}).get("content") or ""
            try:
                answer = _extract_json(text)
            except ValueError as exc:
                raise InvalidAnswer(f"Invalid JSON after one repair: {exc}") from exc
            problems = _validate(answer, schema)
            if problems:
                raise InvalidAnswer("Answer didn't match the schema after one repair: " + "; ".join(problems))
        call.status = "ok"
        return answer, call
    except Exception as exc:
        call.status, call.error = "failed", str(exc)[:500]
        raise
    finally:
        call.requests = _sent_total(session) - sent_before
        call.latency_ms = int((_clock() - t0) * 1000)
        if call.status != "refused":
            _log(session, call)


def _sent_total(session: Session) -> int:
    with _pace_lock:
        return _total_sent


def _add_usage(call: AiCall, data: dict) -> None:
    u = (data or {}).get("usage") or {}
    call.prompt_tokens += int(u.get("prompt_tokens") or 0)
    call.completion_tokens += int(u.get("completion_tokens") or 0)
    try:
        call.cost += float(u.get("cost") or 0.0)
    except (TypeError, ValueError):
        pass
    if (data or {}).get("model"):
        call.model_used = data["model"]


def _log(session: Session, call: AiCall) -> AiCall:
    session.add(call)
    session.commit()
    session.refresh(call)
    return call


def recent_calls(session: Session, limit: int = 30) -> list[dict]:
    rows = session.exec(select(AiCall).order_by(AiCall.id.desc()).limit(limit)).all()
    return [{"id": r.id, "at": r.at.isoformat(), "role": r.role, "models": json.loads(r.models or "[]"),
             "model_used": r.model_used, "status": r.status, "requests": r.requests,
             "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
             "latency_ms": r.latency_ms, "payload_hash": r.payload_hash, "job_id": r.job_id,
             "step_key": r.step_key, "error": r.error} for r in rows]


# ---------- the free model list ----------

_models_cache: tuple[str, list[dict]] | None = None


def free_models() -> dict:
    """Current :free models from OpenRouter (public, no key), cached for the day."""
    global _models_cache
    today = _utc_day()
    if _models_cache and _models_cache[0] == today:
        return {"models": _models_cache[1], "live": True}
    try:
        status, _h, data = _transport.request("GET", f"{BASE}/models", {}, None, 20.0)
        if status != 200:
            raise AiError(str(status))
        out = [{"id": m["id"], "name": m.get("name") or m["id"], "context": m.get("context_length")}
               for m in data.get("data") or [] if str(m.get("id", "")).endswith(":free")]
        out.sort(key=lambda m: m["id"])
        _models_cache = (today, out)
        return {"models": out, "live": True}
    except Exception:
        return {"models": [{"id": m, "name": m, "context": None} for m in KNOWN_FREE], "live": False}


# ---------- the analyst panel's seats ----------

PANEL_SEATS = [{"key": "claude", "label": "Claude", "prefix": "anthropic/claude"},
               {"key": "gpt", "label": "ChatGPT", "prefix": "openai/gpt"},
               {"key": "grok", "label": "Grok", "prefix": "x-ai/grok"}]
# variants that are not the vendor's main model: small, special-purpose or preview builds
_NOT_FLAGSHIP = ("mini", "nano", "haiku", "lite", "flash", "fast", "free", "instant", "codex", "audio", "image", "vision",
                 "search", "oss", "guard", "preview", "beta", "extended", "online", "thinking", "realtime", "chat-latest",
                 "batch", "deep-research", "transcribe", "tts", "embedding", "moderation", "computer-use", "custom", "-chat")
PANEL_MAX_PRICE = 30.0  # USD per million output tokens: skips "pro" tiers that cost ten times more
_catalog_cache: tuple[str, list[dict]] | None = None


def catalog() -> list[dict]:
    """Every model OpenRouter lists (public, no key), with its price, cached for the day."""
    global _catalog_cache
    today = _utc_day()
    if _catalog_cache and _catalog_cache[0] == today:
        return _catalog_cache[1]
    status, _h, data = _transport.request("GET", f"{BASE}/models", {}, None, 20.0)
    if status != 200:
        raise AiError(f"Couldn't read the model list (HTTP {status}).")
    out = []
    for m in data.get("data") or []:
        pr = m.get("pricing") or {}
        try:
            price_in, price_out = float(pr.get("prompt") or 0) * 1e6, float(pr.get("completion") or 0) * 1e6
        except (TypeError, ValueError):
            price_in = price_out = 0.0
        out.append({"id": str(m.get("id", "")), "name": m.get("name") or m.get("id"), "created": int(m.get("created") or 0),
                    "price_in": price_in, "price_out": price_out})
    _catalog_cache = (today, out)
    return out


def flagship(prefix: str, models: list[dict]) -> dict | None:
    """The vendor's newest main model at a normal price: newest by release date among ids with this
    prefix, leaving out small, special-purpose and very expensive variants."""
    # an id with a ":suffix" (":batch", ":free", ":nitro") is a special route of a model, never the model itself
    ok = [m for m in models if m["id"].startswith(prefix) and ":" not in m["id"] and not any(w in m["id"].lower() for w in _NOT_FLAGSHIP)
          and 0 < m["price_out"] <= PANEL_MAX_PRICE]
    return max(ok, key=lambda m: (m["created"], m["id"])) if ok else None


def panel_seats(session: Session) -> list[dict]:
    """The three seats with the model each will use: the one set in Settings, else the newest flagship."""
    from .services import get_settings

    try:
        chosen = {x["key"]: x["model"] for x in json.loads(get_settings(session).ai_panel or "[]") if x.get("model")}
    except (ValueError, TypeError, KeyError):
        chosen = {}
    try:
        models, err = catalog(), ""
    except Exception as exc:
        models, err = [], str(exc)[:200]
    by_id = {m["id"]: m for m in models}
    out = []
    for seat in PANEL_SEATS:
        auto = flagship(seat["prefix"], models)
        mid = chosen.get(seat["key"]) or (auto["id"] if auto else "")
        info = by_id.get(mid) or {}
        out.append({"key": seat["key"], "label": seat["label"], "model": mid, "chosen": seat["key"] in chosen,
                    "auto": auto["id"] if auto else "", "price_in": info.get("price_in"), "price_out": info.get("price_out"),
                    "known": mid in by_id, "error": err if not mid else ""})
    return out
