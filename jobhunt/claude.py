"""Claude through the official Anthropic SDK.

The other providers share one OpenAI-compatible path in llm.py. Claude gets its own, because the native API is what
makes the credit go furthest: prompt caching, an effort level per task, structured JSON output, server-side refusal
fallback and exact token counts (including cache reads, so the usage page shows real spend).
"""
import re
import time

KEY_VAR = "ANTHROPIC_API_KEY"
OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"
FAMILY_DEFAULT = {"opus": OPUS, "sonnet": SONNET, "haiku": HAIKU}

# Which Claude family and effort each part of the app uses unless you pick a model for it in Settings.
# Bulk work runs cheap and quick; writing you'll read or send runs at Claude's best.
TASKS = {
    "screening": ("haiku", "low"),        # every shortlisted job
    "deep_check": ("opus", "medium"),     # the best jobs, checked again
    "planning": ("opus", "low"),          # search queries and spelling fixes
    "cleanup": ("opus", "low"),
    "resume": ("opus", "low"),
    "dashboard": ("opus", "medium"),
    "email": ("opus", "high"),
    "cover_letter": ("opus", "high"),
    "coach": ("opus", "high"),
    "interview": ("opus", "high"),
    "test": ("haiku", "low"),
    "chat": ("opus", "medium"),
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MIN_TOKENS = 2048       # current Claude models always think, and thinking counts toward max_tokens
DATA_URL = re.compile(r"^data:(image/[\w.+-]+);base64,(.+)$", re.S)

_latest = {"at": 0.0, "ids": {}}


class ClaudeError(Exception):
    pass


def client(key, timeout=120):
    import anthropic
    return anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=2)


def list_models(key, timeout=10):
    """Model ids, newest first."""
    models = list(client(key, timeout).with_options(max_retries=0).models.list())
    models.sort(key=lambda m: str(getattr(m, "created_at", "")), reverse=True)
    return [m.id for m in models]


def latest(family, key):
    """The newest model of a family (opus, sonnet, haiku), looked up once a day so "latest" stays latest."""
    if time.monotonic() - _latest["at"] > 86400 or family not in _latest["ids"]:
        try:
            ids = list_models(key)
            for fam in FAMILY_DEFAULT:
                hit = next((i for i in ids if fam in i and not re.search(r"\d{8}$", i)), None)
                if hit:
                    _latest["ids"][fam] = hit
            _latest["at"] = time.monotonic()
        except Exception:
            pass
    return _latest["ids"].get(family) or FAMILY_DEFAULT[family]


def task_model(feature, key):
    family, _ = TASKS.get(feature, TASKS["chat"])
    return latest(family, key)


def effort_for(feature):
    return TASKS.get(feature, TASKS["chat"])[1]


def _block(part):
    if isinstance(part, str):
        return {"type": "text", "text": part}
    if part.get("type") == "text":
        return {"type": "text", "text": part.get("text", "")}
    if part.get("type") == "image_url":
        url = (part.get("image_url") or {}).get("url", "")
        m = DATA_URL.match(url)
        if m:
            return {"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}}
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "text", "text": str(part.get("text") or "")}


def convert(messages):
    """OpenAI-style messages -> (system text, Claude messages). Same-role turns are merged; Claude needs them to alternate."""
    system, out = [], []
    for m in messages:
        role, content = m["role"], m.get("content") or ""
        if role == "system":
            system.append(content if isinstance(content, str) else "\n".join(_block(p).get("text", "") for p in content))
            continue
        blocks = [_block(content)] if isinstance(content, str) else [_block(p) for p in content]
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continuing)"}]})
    return "\n\n".join(s for s in system if s), out


def _text(r):
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()


def request_params(messages, model, feature="chat", max_tokens=1200, schema=None):
    """The request body shared by single calls and batches."""
    system, msgs = convert(messages)
    output_config = {"effort": effort_for(feature)}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kw = dict(model=model, max_tokens=max(max_tokens, MIN_TOKENS), messages=msgs, output_config=output_config,
              cache_control={"type": "ephemeral"})          # caches the shared prefix: system prompt, profile, history
    if system:
        kw["system"] = system
    return kw


def _usage(u):
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    return {"tokens_in": (u.input_tokens or 0) + cache_read + cache_write, "tokens_out": u.output_tokens or 0,
            "cache_read": cache_read, "cache_write": cache_write}


def batch(items, key, model, feature="screening", max_tokens=1200, schema=None, on_progress=None, max_wait=3600, poll=20):
    """Many requests through the Message Batches API at half price. Usually done in minutes, at most `max_wait`
    seconds before we give up and cancel. items: [(id, messages)]. Returns {id: (text or None, usage or None)}."""
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    c = client(key)
    try:
        b = c.messages.batches.create(requests=[
            Request(custom_id=str(cid), params=MessageCreateParamsNonStreaming(**request_params(msgs, model, feature, max_tokens, schema)))
            for cid, msgs in items])
        waited = 0
        while b.processing_status != "ended":
            if waited >= max_wait:
                c.messages.batches.cancel(b.id)
                raise ClaudeError("Claude's batch didn't finish in time; it was cancelled.")
            time.sleep(poll)
            waited += poll
            b = c.messages.batches.retrieve(b.id)
            if on_progress:
                n = b.request_counts
                on_progress(n.succeeded + n.errored + n.canceled + n.expired, len(items))
        out = {}
        for res in c.messages.batches.results(b.id):           # results come in any order: key them by id
            if res.result.type == "succeeded":
                msg = res.result.message
                out[res.custom_id] = (None, None) if msg.stop_reason == "refusal" else (_text(msg) or None, _usage(msg.usage))
            else:
                out[res.custom_id] = (None, None)
        return out
    except anthropic.APIError as e:
        raise ClaudeError(f"Claude batch: {getattr(e, 'message', e)}"[:300]) from e


def complete(messages, key, model, feature="chat", max_tokens=1200, schema=None, timeout=120):
    """One request. Returns (text, model that answered, usage dict). Raises ClaudeError on any failure."""
    import anthropic
    kw = request_params(messages, model, feature, max_tokens, schema)
    c = client(key, timeout)
    try:
        if model.startswith("claude-opus") or model.startswith("claude-fable"):
            # a policy refusal is re-answered server-side by a suitable model instead of failing the task
            r = c.beta.messages.create(**kw, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            r = c.messages.create(**kw)
    except anthropic.AuthenticationError as e:
        raise ClaudeError("Claude rejected the key.") from e
    except anthropic.RateLimitError as e:
        raise ClaudeError("Claude: rate limit or no credit left.") from e
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Claude: {getattr(e, 'message', e)}"[:300]) from e
    except anthropic.APIError as e:                 # connection errors, timeouts
        raise ClaudeError(f"Claude couldn't be reached: {e}"[:300]) from e
    if r.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request.")
    text = _text(r)
    if not text:
        raise ClaudeError("Claude returned an empty answer" + (" (it ran out of room)" if r.stop_reason == "max_tokens" else "") + ".")
    return text, getattr(r, "model", None) or model, _usage(r.usage)
