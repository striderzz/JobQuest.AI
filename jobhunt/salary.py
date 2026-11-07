"""Salaries written in job postings, as yearly amounts. Nothing is ever estimated: no figure in the text means no salary.

Most Indian postings don't show pay, so expect low coverage; the dashboard says how many jobs a median is based on.
"""
import re

NUM = r"(\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)"
RANGE = rf"{NUM}\s*(k|K)?\s*(?:-|–|—|to)\s*(?:₹|rs\.?|inr|\$|usd)?\s*{NUM}"
PAY_WORDS = re.compile(r"salary|ctc|compensation|stipend|pay\b|package|remuneration|per annum|p\.a\.|lpa", re.I)
NOT_PAY = re.compile(r"funding|funded|raised|revenue|valuation|turnover|\barr\b|investment|customers|users|downloads|"
                     r"learners|students|merchants|employees|million|billion|crore", re.I)
MONTH = r"(?:per\s*month|p\.?\s*m\.?\b|/\s*(?:month|mo\b)|a\s+month|monthly)"
INR_MARK = r"(?:₹|rs\.?\s|inr\s*)"
USD_MARK = r"(?:\$|usd\s*)"

# yearly limits that keep typos and unrelated numbers out
LIMITS = {"INR": (60_000, 30_000_000), "USD": (15_000, 900_000)}


def _n(s):
    return float(s.replace(",", ""))


def _result(lo, hi, currency, src):
    lo, hi = min(lo, hi), max(lo, hi)
    a, b = LIMITS[currency]
    if lo < a or hi > b:
        return None
    return {"salary_min": round(lo), "salary_max": round(hi), "salary_currency": currency, "salary_src": src}


def _near(text, m, width=70):
    return text[max(0, m.start() - width): m.end() + width]


def parse(text, src="posted"):
    """The first salary stated in `text`, or None."""
    t = text or ""
    # 12-18 LPA, 12 to 18 lakhs per annum, INR 8 LPA, 6.5 lakh CTC
    for m in re.finditer(rf"{INR_MARK}?(?:{NUM}\s*(?:-|–|—|to)\s*{INR_MARK}?)?{NUM}\s*(lpa|lakhs?|lacs?)\b", t, re.I):
        area = _near(t, m)
        if m.group(3).lower() != "lpa" and (not PAY_WORDS.search(area) or NOT_PAY.search(t[m.end():m.end() + 30])):
            continue
        hi = _n(m.group(2)) * 100_000
        lo = _n(m.group(1)) * 100_000 if m.group(1) else hi
        got = _result(lo, hi, "INR", src)
        if got:
            return got
    # ₹50,000 per month, Rs. 30000 - 40000 /month, stipend of 25000 per month
    for m in re.finditer(rf"({INR_MARK})?(?:{NUM}\s*(?:-|–|—|to)\s*{INR_MARK}?)?{NUM}\s*{MONTH}", t, re.I):
        if not m.group(1) and not PAY_WORDS.search(_near(t, m)):
            continue
        hi = _n(m.group(3)) * 12
        lo = _n(m.group(2)) * 12 if m.group(2) else hi
        got = _result(lo, hi, "INR", src)
        if got:
            return got
    # ₹12,00,000 - ₹18,00,000 a year
    for m in re.finditer(rf"{INR_MARK}{RANGE}|{INR_MARK}{NUM}", t, re.I):
        if NOT_PAY.search(_near(t, m, 40)):
            continue
        hi = _n(m.group(3) or m.group(4))
        lo = _n(m.group(1)) if m.group(1) else hi
        got = _result(lo, hi, "INR", src)
        if got:
            return got
    # $80,000 - $120,000, USD 90k
    for m in re.finditer(rf"{USD_MARK}{RANGE}|{USD_MARK}{NUM}\s*(k|K)?", t, re.I):
        if NOT_PAY.search(_near(t, m, 40)) or not PAY_WORDS.search(_near(t, m)):
            continue
        if m.group(1):
            hi = _n(m.group(3))
            lo = _n(m.group(1))
            if m.group(2) or hi < 1000:
                lo, hi = lo * 1000, hi * 1000
        else:
            hi = lo = _n(m.group(4)) * (1000 if m.group(5) else 1)
        got = _result(lo, hi, "USD", src)
        if got:
            return got
    return None


def from_input(text):
    """What you typed in the tracker: '12-18 LPA', '8 lakh', '50000 per month', or a plain yearly rupee figure."""
    t = (text or "").strip()
    if not t:
        return {"salary_min": None, "salary_max": None, "salary_currency": "", "salary_src": ""}
    got = parse(f"salary {t}" if re.search(r"[a-z₹$]", t, re.I) else f"₹{t}", "you")
    if not got:
        raise ValueError("Couldn't read that salary. Try 12-18 LPA, 8 lakh or 50000 per month.")
    return got


def from_board(lo, hi, currency="INR", period="year", src="board"):
    """Figures a job board gives as fields. Only called for real values, never the board's predicted ones."""
    try:
        lo, hi = float(lo or hi or 0), float(hi or lo or 0)
    except (TypeError, ValueError):
        return None
    if not hi:
        return None
    mult = {"hour": 2080, "day": 260, "week": 52, "month": 12}.get((period or "year").lower().rstrip("ly"), 1)
    currency = (currency or "INR").upper()
    return _result(lo * mult, hi * mult, currency, src) if currency in LIMITS else None


def label(job):
    """12–18 LPA, or $90k–$120k."""
    lo, hi = job.get("salary_min"), job.get("salary_max")
    if not hi:
        return ""
    if job.get("salary_currency") == "USD":
        return f"${lo / 1000:g}k" if lo == hi else f"${lo / 1000:g}k–${hi / 1000:g}k"
    a, b = round(lo / 100_000, 1), round(hi / 100_000, 1)
    return f"{a:g} LPA" if a == b else f"{a:g}–{b:g} LPA"
