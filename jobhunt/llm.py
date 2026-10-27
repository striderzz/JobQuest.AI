"""One chat interface over many LLM providers.

Every provider here speaks the OpenAI chat-completions protocol (natively or through its OpenAI-compatible endpoint),
so one SDK covers them all and only the base URL, key and model differ. Keys live in ~/.jobhuntai/.env; which
providers work and in what order to try them is kept in the settings table.
"""
import json
import re
import time
import urllib.request

from . import db, settings

# id: (label, base_url, default model, key env var, free tier?, model-name hint used when the default is gone, key page)
PROVIDERS = {
    "openai": ("OpenAI", None, "gpt-6-luna", "OPENAI_API_KEY", False, "gpt", "platform.openai.com/api-keys"),
    "anthropic": ("Anthropic Claude", "https://api.anthropic.com/v1/", "claude-haiku-5-5", "ANTHROPIC_API_KEY", False,
                  "haiku", "console.anthropic.com/settings/keys"),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash",
               "GEMINI_API_KEY", True, "flash", "aistudio.google.com/apikey"),
    "groq": ("Groq (Llama, fast)", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY", True,
             "llama", "console.groq.com/keys"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com", "deepseek-chat", "DEEPSEEK_API_KEY", False, "chat",
                 "platform.deepseek.com/api_keys"),
    "xai": ("xAI Grok", "https://api.x.ai/v1", "grok-4-fast", "XAI_API_KEY", False, "grok", "console.x.ai"),
    "kimi": ("Moonshot Kimi", "https://api.moonshot.ai/v1", "kimi-latest", "MOONSHOT_API_KEY", False, "kimi",
             "platform.moonshot.ai/console/api-keys"),
    "mistral": ("Mistral", "https://api.mistral.ai/v1", "mistral-small-latest", "MISTRAL_API_KEY", True, "small",
                "console.mistral.ai/api-keys"),
    "openrouter": ("OpenRouter (many free models)", "https://openrouter.ai/api/v1",
                   "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY", True, ":free", "openrouter.ai/keys"),
    "ollama": ("Local model (Ollama)", "http://127.0.0.1:11434/v1", "llama3.2", None, True, "llama", "ollama.com/download"),
}
KEY_VARS = [p[3] for p in PROVIDERS.values() if p[3]]
NO_KEY = "Please add an LLM key: open Settings → AI providers and paste a key for any provider (Gemini, Groq and OpenRouter have free tiers)."


class LLMError(Exception):
    pass


def _model_var(pid):
    return f"LLM_MODEL_{pid.upper()}"


def model_for(pid, env=None):
    env = env if env is not None else settings.read_env()
    if pid == "openai":
        return env.get("OPENAI_MODEL") or settings.load_config()["ai"]["model"]
    if env.get(_model_var(pid)):
        return env[_model_var(pid)]
    if pid == "ollama":                      # use what's installed: a Llama if there is one, else the first model
        have = ollama_models()
        if have and not any(m.split(":")[0] == PROVIDERS[pid][2] for m in have):
            return next((m for m in have if "llama" in m.lower()), have[0])
    return PROVIDERS[pid][2]


_ollama_cache = [0.0, []]


def ollama_models(timeout=1.0):
    """Model names installed in a running Ollama, or [] when it isn't running. Cached for 30 s (the UI polls)."""
    if time.monotonic() - _ollama_cache[0] < 30:
        return _ollama_cache[1]
    try:
        # 127.0.0.1, not localhost: on Windows localhost tries IPv6 first and Ollama only listens on IPv4
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        names = []
    _ollama_cache[:] = [time.monotonic(), names]
    return names


def order():
    saved = [p for p in (db.get_setting("llm_order") or []) if p in PROVIDERS]
    return saved + [p for p in PROVIDERS if p not in saved]


def configured(env=None, check_ollama=True):
    """Providers that have what they need (a key, or a running Ollama), in preferred order."""
    env = env if env is not None else settings.read_env()
    out = []
    for pid in order():
        var = PROVIDERS[pid][3]
        if var and env.get(var):
            out.append(pid)
        elif pid == "ollama" and check_ollama and ollama_models():
            out.append(pid)
    return out


def client(pid, env=None):
    from openai import OpenAI
    env = env if env is not None else settings.read_env()
    label, base, _, var, *_ = PROVIDERS[pid]
    key = env.get(var) if var else "ollama"
    if not key:
        raise LLMError(f"No key saved for {label}.")
    timeout = 180 if pid == "ollama" else 90
    return OpenAI(api_key=key, base_url=base, timeout=timeout, max_retries=1)


def _short(e):
    from .ai import _short as s
    return s(e)


def _complete(pid, messages, max_tokens, env=None, model=None):
    import openai
    c = client(pid, env)
    model = model or model_for(pid, env)
    kw = dict(model=model, messages=messages, max_completion_tokens=max_tokens)
    for _ in range(2):
        try:
            r = c.chat.completions.create(**kw)
            break
        except openai.BadRequestError as e:
            if "max_completion_tokens" in str(e) and "max_completion_tokens" in kw:   # older compatible servers
                kw["max_tokens"] = kw.pop("max_completion_tokens")
                continue
            raise
    text = (r.choices[0].message.content or "") if r.choices else ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()        # reasoning models (qwen, deepseek-r1)
    if not text:
        raise LLMError(f"{PROVIDERS[pid][0]} returned an empty answer.")
    return text


def _pick_model(pid, env):
    """When the default model is gone, choose an available one whose name contains the provider's hint."""
    hint = PROVIDERS[pid][5]
    try:
        if pid == "ollama":
            names = ollama_models()
        else:
            names = sorted(m.id for m in client(pid, env).models.list())
    except Exception:
        return None
    good = [n for n in names if hint in n.lower() and not any(x in n.lower() for x in ("embed", "tts", "whisper", "image", "audio", "guard", "vision"))]
    return (good or names or [None])[0]


def test(pid, key=None, model=None):
    """Save the key/model and prove they work with a tiny request. Returns the model actually used."""
    import openai
    env = settings.read_env()
    var = PROVIDERS[pid][3]
    if key:
        env[var] = key
    if model:
        env[_model_var(pid) if pid != "openai" else "OPENAI_MODEL"] = model
    if var and not env.get(var):
        raise LLMError(f"Paste your {PROVIDERS[pid][0]} key first.")
    if pid == "ollama" and not ollama_models():
        raise LLMError("Ollama isn't running. Install it from ollama.com, then run: ollama pull llama3.2")
    used = model_for(pid, env)
    try:
        try:
            _complete(pid, [{"role": "user", "content": "Reply with the single word OK."}], 16, env, used)
        except (openai.NotFoundError, openai.BadRequestError) as e:
            if "model" not in str(e).lower():
                raise
            alt = _pick_model(pid, env)
            if not alt or alt == used:
                raise
            _complete(pid, [{"role": "user", "content": "Reply with the single word OK."}], 16, env, alt)
            used = alt
    except openai.AuthenticationError as e:
        raise LLMError(f"{PROVIDERS[pid][0]} rejected the key.") from e
    except openai.RateLimitError as e:
        raise LLMError(f"{PROVIDERS[pid][0]}: rate limit or no credit left ({_short(e)}).") from e
    except (openai.APIError, LLMError) as e:
        raise LLMError(f"{PROVIDERS[pid][0]}: {_short(e)}") from e
    updates = {}
    if key:
        updates[var] = key
    if pid == "openai":
        updates["OPENAI_MODEL"] = used
    else:
        updates[_model_var(pid)] = used if used != PROVIDERS[pid][2] else None
    settings.write_env(updates)
    return used


def remove(pid):
    var = PROVIDERS[pid][3]
    settings.write_env({k: None for k in (var, _model_var(pid)) if k})


def status():
    env = settings.read_env()
    have_ollama = ollama_models()
    ready = configured(env, check_ollama=False) + (["ollama"] if have_ollama else [])
    rows = []
    for pid in order():
        label, _, default, var, free, _, page = PROVIDERS[pid]
        key = env.get(var) if var else ""
        rows.append({"id": pid, "label": label, "free": free, "key_page": page, "needs_key": bool(var),
                     "ready": pid in ready, "key_hint": f"…{key[-4:]}" if key and len(key) > 8 else "",
                     "model": model_for(pid, env), "default_model": default,
                     "local_models": have_ollama if pid == "ollama" else []})
    return {"providers": rows, "active": [p for p in order() if p in ready]}


def chat(messages, max_tokens=1200, prefer=None):
    """Answer with the first provider that works (``prefer`` first if it's ready). Returns (text, provider label)."""
    import openai
    env = settings.read_env()
    ready = configured(env)
    if prefer in ready:
        ready.remove(prefer)
        ready.insert(0, prefer)
    if not ready:
        raise LLMError(NO_KEY)
    errors = []
    for pid in ready:
        try:
            return _complete(pid, messages, max_tokens, env), PROVIDERS[pid][0]
        except (openai.APIError, LLMError) as e:              # bad key, quota, outage: try the next provider
            errors.append(f"{PROVIDERS[pid][0]}: {_short(e)}")
    raise LLMError("No AI provider answered. " + " | ".join(errors)[:600])
