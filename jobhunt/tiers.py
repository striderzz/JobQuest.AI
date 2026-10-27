"""Company tiers (Big Tech, US unicorns with India offices, Indian startups, everyone else) from config.toml [tiers]."""
import re
from functools import lru_cache

from . import settings

LABELS = {"big_tech": "Big Tech (FAANG / MANGO)", "us_unicorn": "US unicorn / scale-up", "startup": "Indian startup",
          "mid": "Mid-size & enterprise"}
ORDER = ("big_tech", "us_unicorn", "startup")


@lru_cache(maxsize=1)
def _patterns():
    cfg = settings.load_config().get("tiers", {})
    out = []
    for tier in ORDER:
        names = sorted({n.strip().lower() for n in cfg.get(tier, []) if n.strip()}, key=len, reverse=True)
        if names:
            out.append((tier, re.compile(r"(?<![\w.-])(" + "|".join(re.escape(n) for n in names) + r")(?![\w-])")))
    return out


@lru_cache(maxsize=4096)
def tier_of(company):
    name = (company or "").lower()
    for tier, rx in _patterns():
        if rx.search(name):
            return tier
    return "mid"
