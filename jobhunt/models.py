from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


@dataclass
class Job:
    source: str                       # where it came from: "Greenhouse", "amazon.jobs", "Himalayas", ...
    company: str
    title: str
    location: str
    url: str                          # the page to apply from
    posted: Optional[datetime]        # first-published time, UTC
    description: str = ""
    hints: set = field(default_factory=set)   # source-supplied level flags: "intern", "entry", "university"
    detail: Optional[Callable[["Job"], None]] = None   # lazy fetch that fills description/location

    # filled in by filters.py
    kind: str = ""                    # "SDE" or "ML"
    level: str = ""                   # "Intern", "New grad", "0-2 yrs", ...
    min_years: Optional[int] = None
    remote: str = ""                  # "" on-site/hybrid in India, "India" remote within India
    cities: list = field(default_factory=list)   # canonical Indian cities named in the location
    key: str = ""                     # dedupe key
    also_on: list = field(default_factory=list)

    # filled in by match.py
    score: float = 0.0
    parts: dict = field(default_factory=dict)
    matched: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    resume: str = ""                  # which resume fits best (label)

    # filled in by store.py
    is_new: bool = False
    first_seen: str = ""
    status: str = ""                  # "", "applied", "saved", "hidden"

    def age_days(self, now):
        return None if self.posted is None else max(0, (now - self.posted).days)

    def to_dict(self, now):
        d = {k: v for k, v in self.__dict__.items() if k not in ("detail", "description", "hints")}
        d["posted"] = self.posted.isoformat() if self.posted else None
        d["age_days"] = self.age_days(now)
        return d
