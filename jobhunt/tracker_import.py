"""Fill the application tracker from an Excel (.xlsx) or CSV file.

Needed columns: company, role, date. Optional: location, link, status, notes. Header names are matched loosely
("Company Name", "Comp", "Position", "Applied On", ...). Rows without a location keep it blank.
"""
import csv
import io
import re
from datetime import date, datetime

COLUMNS = {
    "company": ["company", "company name", "comp", "comp name", "organisation", "organization", "employer", "firm"],
    "role": ["role", "title", "job title", "position", "job", "designation", "job role"],
    "date": ["date", "applied", "applied on", "date applied", "applied date", "application date", "day"],
    "location": ["location", "city", "place", "loc"],
    "url": ["link", "url", "job link", "posting", "job url"],
    "status": ["status", "stage", "result"],
    "notes": ["notes", "note", "remarks", "comments", "comment"],
    "job_type": ["job type", "type", "employment type", "work type"],
    "app_source": ["source", "channel", "via", "applied via", "platform"],
    "last_update": ["last update", "last updated", "updated", "last contact"],
    "next_step": ["next step", "next steps", "next action", "follow up", "followup"],
    "contact": ["contact", "recruiter", "hr", "hr contact", "point of contact", "poc"],
}
STATUS_WORDS = [("offer", "offer"), ("screen", "screening"), ("interview", "interviewing"), ("oa", "oa"), ("test", "oa"), ("assessment", "oa"),
                ("assignment", "oa"), ("reject", "rejected"), ("declin", "rejected"), ("ghost", "ghosted"),
                ("no response", "ghosted"), ("withdr", "withdrawn"), ("saved", "saved"), ("wishlist", "saved"),
                ("applied", "applied")]


class ImportError_(Exception):
    pass


def _norm(s):
    return re.sub(r"[^a-z ]", "", str(s or "").lower()).strip()


def _rows(filename, data):
    if filename.lower().endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    if filename.lower().endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return list(csv.reader(io.StringIO(text)))
    raise ImportError_("Use an Excel .xlsx or a .csv file.")


def _date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
                "%d/%m/%y", "%d-%b-%Y", "%d-%b-%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _status(v):
    words = re.findall(r"[a-z]+", str(v or "").lower())
    text = " ".join(words)
    for word, st in STATUS_WORDS:
        if (word in words) if len(word) <= 4 else (word in text):      # "oa" / "test" only as whole words
            return st
    return "applied"


def _iso(v):
    d = _date(v)
    return d.isoformat() if d else ""


def parse(filename, data):
    """Return (entries, problems). Each entry: company, role, date (ISO), location, url, status, notes."""
    rows = [r for r in _rows(filename, data) if any(str(c or "").strip() for c in r)]
    if not rows:
        raise ImportError_("The file is empty.")
    head = [_norm(c) for c in rows[0]]
    col = {}
    for field, names in COLUMNS.items():
        for i, h in enumerate(head):
            if h in names or any(h.startswith(n) for n in names if len(n) > 3):
                col.setdefault(field, i)
    missing = [f for f in ("company", "role") if f not in col]
    if missing:
        raise ImportError_(f"Couldn't find a {' or '.join(missing)} column. The first row should have headings like "
                           f"Company, Role, Date.")
    def cell(r, f):
        v = r[col[f]] if f in col and col[f] < len(r) else None
        # JobHuntAI's own CSV export guards formula-like text with a leading apostrophe; drop it again
        return v[1:] if isinstance(v, str) and v.startswith("'") and v[1:2] in ("=", "+", "@", "-") else v
    entries, problems = [], []
    for n, r in enumerate(rows[1:], start=2):
        company, role = str(cell(r, "company") or "").strip(), str(cell(r, "role") or "").strip()
        if not company and not role:
            continue
        if not company or not role:
            problems.append(f"Row {n}: needs both a company and a role")
            continue
        d = _date(cell(r, "date"))
        if cell(r, "date") not in (None, "") and d is None:
            problems.append(f"Row {n}: couldn't read the date '{cell(r, 'date')}', used today")
        entries.append({"company": company[:120], "role": role[:200], "date": (d or date.today()).isoformat(),
                        "location": str(cell(r, "location") or "").strip()[:120],
                        "url": str(cell(r, "url") or "").strip()[:500],
                        "status": _status(cell(r, "status")) if "status" in col else "applied",
                        "notes": str(cell(r, "notes") or "").strip()[:2000],
                        "extra": {"job_type": str(cell(r, "job_type") or "").strip()[:60],
                                  "app_source": str(cell(r, "app_source") or "").strip()[:80],
                                  "last_update": _iso(cell(r, "last_update")),
                                  "next_step": str(cell(r, "next_step") or "").strip()[:300],
                                  "contact": str(cell(r, "contact") or "").strip()[:200]}})
    return entries, problems


TEMPLATE_CSV = ("Date Applied,Company,Role,Job Type,Location,Source,Job Link,Status,Last Update,Next Step,"
                "Contact,Notes\n21/10/2026,Acme Pvt Ltd,Software Engineer,Full-time,,Referral,,Applied,,"
                "Follow up in a week,,Referral from Ravi\n")
