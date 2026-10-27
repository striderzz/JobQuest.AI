"""AI layer (OpenAI or any compatible provider): read a resume into a structured profile, and screen each job against it.

Uses the official SDK's structured outputs (chat.completions.parse with a Pydantic schema), so every answer
arrives as validated fields rather than free text. The candidate block is identical across all job checks in a
refresh and comes first in the prompt, which lets OpenAI's prompt cache reuse it.
"""
import json
import threading
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import settings


class AIError(Exception):
    pass


# ---------------------------------------------------------------- schemas

class Education(BaseModel):
    degree: str = Field(description="e.g. MS, B.Tech, BE")
    field: str = Field(description="e.g. Computer Engineering")
    school: str
    end: str = Field(description="Graduation month and year as written, e.g. 'Aug 2026', or ''")


class ResumeInsights(BaseModel):
    headline: str = Field(description="One-line professional headline")
    seniority: Literal["student", "new_grad", "junior"]
    fulltime_experience_months: int = Field(description="Paid full-time work after the first degree; internships excluded")
    internship_months: int = Field(description="Internships, co-ops and summer roles combined")
    skills: list[str] = Field(description="Technical skills, canonical names, most important first, max 40")
    target_roles: list[Literal["SDE", "ML"]] = Field(description="Job families this resume is aimed at")
    strengths: list[str] = Field(description="3-5 concrete strengths with evidence from the resume")
    education: list[Education]


class Contact(BaseModel):
    name: str = Field(description="Person's name as written, or ''")
    role: str = Field(description="e.g. 'Recruiter', 'HR', 'Hiring manager', or ''")
    email: str = Field(description="Email exactly as written in the posting, or ''")
    phone: str = Field(description="Phone exactly as written in the posting, or ''")


class JobAssessment(BaseModel):
    years_required: Optional[int] = Field(
        description="Minimum years of professional experience the posting REQUIRES (ignore 'preferred' or "
                    "'nice to have'); null if it states none")
    entry_level_ok: bool = Field(description="True only if a new graduate with under 3 years of experience could "
                                             "realistically be hired: intern, new grad, fresher, junior or 0-2 years")
    in_india: bool = Field(description="True only if the job is based in India or is remote within India")
    work_mode: Literal["onsite", "hybrid", "remote", "unknown"]
    fit_score: int = Field(description="0-100 match of the candidate to this job's must-haves")
    verdict: Literal["strong", "good", "stretch", "poor"] = Field(
        description="strong >= 85, good 70-84, stretch 50-69, poor < 50")
    reasons: list[str] = Field(description="2-3 short, specific reasons citing the candidate's skills or projects")
    missing_skills: list[str] = Field(description="Up to 5 must-have skills the candidate lacks")
    must_haves: list[str] = Field(description="Up to 5 key requirements from the posting")
    best_resume: str = Field(description="Exact label of the candidate's resume to send, or ''")
    role_match: bool = Field(description="True only if this is a software engineering or ML / AI / data science / "
                                         "data engineering role. False for hardware, embedded-only firmware, "
                                         "mechanical, PLC, IT admin, HR systems, sales, support or data labelling.")
    contacts: list[Contact] = Field(
        description="Recruiters, HR or hiring managers EXPLICITLY named in the posting text with their email or phone. "
                    "Never guess, infer or construct contact details. Skip privacy, accessibility, legal and no-reply "
                    "addresses. Empty list if none.")


# Bump when JobAssessment changes, so cached verdicts are re-checked with the new fields.
SCHEMA_VERSION = 3

RESUME_SYSTEM = ("You extract a structured profile from a resume. Use only what the resume says. Count internships "
                 "and summer roles as internship months even if titled 'Engineer'. Skills: technical only, no soft "
                 "skills.")
JOB_SYSTEM = (
    "You screen job postings for one candidate looking for entry-level tech jobs in India. Be strict and factual: use "
    "only the posting text and the candidate profile, never assume. The candidate wants SDE and/or ML roles with "
    "under 3 years of required experience, located in India.\n"
    "Scoring: weigh the posting's must-have skills and level against the candidate's skills, projects and experience. "
    "A role demanding 3+ years, or not in India, is never a fit.\n\n")


# ---------------------------------------------------------------- client

class AI:
    label = "OpenAI"
    json_mode = False                   # set when the provider can't do strict JSON-schema output
    def __init__(self, api_key, model, reasoning_effort=None, prices=None, base_url=None, label="OpenAI"):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180 if "11434" in (base_url or "") else 90,
                             max_retries=2)
        self.model = model
        self.label = label
        self.effort = reasoning_effort or None
        self.price = (prices or {}).get(model)
        self.tokens_in = self.tokens_out = 0
        self._lock = threading.Lock()

    def check(self):
        """Raise if the key or model is wrong. One cheap metadata call, no tokens."""
        self.client.models.retrieve(self.model)

    def cost(self, tokens_in=None, tokens_out=None):
        if not self.price:
            return None
        ti = self.tokens_in if tokens_in is None else tokens_in
        to = self.tokens_out if tokens_out is None else tokens_out
        return round((ti * self.price[0] + to * self.price[1]) / 1_000_000, 5)

    def _parse(self, schema, messages, max_tokens, cache_key=None):
        import openai
        if self.json_mode:
            return self._parse_json(schema, messages, max_tokens)
        kw = dict(model=self.model, messages=messages, response_format=schema, max_completion_tokens=max_tokens)
        if self.effort:
            kw["reasoning_effort"] = self.effort
        if cache_key:
            kw["prompt_cache_key"] = cache_key
        for _ in range(3):
            try:
                r = self.client.chat.completions.parse(**kw)
                break
            except openai.BadRequestError as e:
                msg = str(e).lower()
                if "reasoning" in msg and "reasoning_effort" in kw:     # model doesn't take the parameter
                    self.effort = None
                    kw.pop("reasoning_effort")
                elif "prompt_cache_key" in msg and "prompt_cache_key" in kw:
                    kw.pop("prompt_cache_key")
                elif "response_format" in msg or "json_schema" in msg or "schema" in msg:
                    self.json_mode = True
                    return self._parse_json(schema, messages, max_tokens)
                else:
                    raise AIError(_short(e)) from e
            except openai.AuthenticationError as e:
                raise AIError(f"{self.label} rejected the API key. Check it on the Settings page.") from e
            except openai.PermissionDeniedError as e:
                raise AIError(f"This key can't use model '{self.model}': {_short(e)}") from e
            except openai.NotFoundError as e:
                raise AIError(f"Model '{self.model}' isn't available to this key.") from e
            except openai.RateLimitError as e:
                raise AIError(f"{self.label} rate limit or quota reached (check your plan or billing).") from e
            except openai.APIError as e:
                raise AIError(_short(e)) from e
        else:
            raise AIError(f"{self.label} kept rejecting the request parameters.")
        if r.usage:
            with self._lock:
                self.tokens_in += r.usage.prompt_tokens or 0
                self.tokens_out += r.usage.completion_tokens or 0
        msg = r.choices[0].message
        if getattr(msg, "refusal", None):
            raise AIError(f"Model refused: {msg.refusal}")
        if msg.parsed is None:
            raise AIError("Model returned no structured answer (output may have been cut off).")
        return msg.parsed

    def _parse_json(self, schema, messages, max_tokens):
        """Fallback for providers without strict structured output: ask for JSON matching the schema, then validate."""
        import openai
        from pydantic import ValidationError
        spec = json.dumps(schema.model_json_schema())
        msgs = [dict(m) for m in messages]
        msgs[0]["content"] += f"\n\nAnswer with one JSON object only, no prose, matching this JSON schema:\n{spec}"
        last = None
        for _ in range(2):
            try:
                r = self.client.chat.completions.create(model=self.model, messages=msgs, max_tokens=max_tokens,
                                                        response_format={"type": "json_object"})
            except openai.BadRequestError:
                r = self.client.chat.completions.create(model=self.model, messages=msgs, max_tokens=max_tokens)
            except openai.APIError as e:
                raise AIError(f"{self.label}: {_short(e)}") from e
            if r.usage:
                with self._lock:
                    self.tokens_in += r.usage.prompt_tokens or 0
                    self.tokens_out += r.usage.completion_tokens or 0
            text = (r.choices[0].message.content or "").strip()
            text = text[text.find("{"):text.rfind("}") + 1] if "{" in text else text
            try:
                return schema.model_validate_json(text)
            except ValidationError as e:
                last = e
        raise AIError(f"{self.label} didn't return valid JSON: {str(last)[:200]}")

    def read_resume(self, text):
        out = self._parse(ResumeInsights, [{"role": "system", "content": RESUME_SYSTEM},
                                           {"role": "user", "content": text[:30000]}], 4000)
        return out.model_dump()

    def assess(self, candidate_block, job, cache_key=None):
        user = (f"JOB\nTitle: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}\n"
                f"Posted: {(job.get('posted') or '')[:10]}\n\n{(job.get('description') or '')[:7000]}")
        out = self._parse(JobAssessment, [{"role": "system", "content": JOB_SYSTEM + candidate_block},
                                          {"role": "user", "content": user}], 2500, cache_key)
        d = out.model_dump()
        d["fit_score"] = max(0, min(100, int(d["fit_score"])))
        return d


def _short(e):
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        m = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("message")
        if m:
            return str(m)[:300]
    return str(e)[:300]


def rejected(assessment, max_years=2):
    """True when the AI says the job breaks a hard rule: 3+ years required, not entry level, or not in India."""
    # Stated years decide the level (2+ years still fits "under 3"); the AI's own judgement only fills the gap
    # when the posting gives no number.
    y = assessment.get("years_required")
    too_senior = (y > max_years) if y is not None else not assessment.get("entry_level_ok", True)
    return too_senior or (not assessment.get("in_india", True)) or (not assessment.get("role_match", True))


def client_from_settings(cfg=None):
    """An AI client for the first ready provider (Settings -> AI providers order), else None."""
    from . import llm
    cfg = cfg or settings.load_config()
    env = settings.read_env()
    # Bulk job screening needs a strong, fast model; a small local model is kept for cover letters and the Coach
    # unless [ai] screen_with_local = true in config.toml.
    ready = [p for p in llm.configured(env) if p != "ollama" or cfg["ai"].get("screen_with_local")]
    if not ready:
        return None
    pid = ready[0]
    label, base, _, var, *_ = llm.PROVIDERS[pid]
    a = cfg["ai"]
    model = llm.model_for(pid, env)
    effort = a.get("reasoning_effort") if pid == "openai" else None
    return AI(env.get(var) if var else "ollama", model, effort, a.get("prices"), base, label)


def key_hint():
    key = settings.read_env().get("OPENAI_API_KEY") or ""
    return f"…{key[-4:]}" if len(key) > 8 else ""
