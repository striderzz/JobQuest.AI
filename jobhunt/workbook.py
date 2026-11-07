"""The tracker as an Excel workbook: every application, then the same analysis the dashboard shows."""
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import db, salary, stats

APP_COLUMNS = [("Date Applied", "applied_on"), ("Company", "company"), ("Role", "title"), ("Role Family", None), ("Status", "status"),
               ("Job Type", "job_type"), ("Location", "location"), ("Source", "app_source"), ("Salary", None), ("Job Link", "url"),
               ("Last Update", "last_update"), ("Next Step", "next_step"), ("Contact", "contact"), ("Notes", "notes")]
TABLE_COLUMNS = [("Applied", "applied"), ("Active", "active"), ("Replies", "replies"), ("Reached screening+", "interviews"),
                 ("Offers", "offers"), ("Rejected", "rejected"), ("Ghosted", "ghosted"), ("Reply rate %", "reply_rate"),
                 ("First", "first"), ("Last", "last"), ("Median days to reply", "median_wait")]


def _safe(v):
    """Text from job postings must not run as a formula. The importer removes this apostrophe again."""
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


def _sheet(wb, title, header, rows):
    ws = wb.create_sheet(title)
    ws.append(header)
    for row in rows:
        ws.append([_safe(v) for v in row])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, h in enumerate(header, 1):
        width = max([len(str(h))] + [len(str(r[i - 1] or "")) for r in rows[:200]])
        ws.column_dimensions[get_column_letter(i)].width = min(46, width + 2)
    return ws


def build(today=None):
    today = today or date.today()
    rows = db.list_jobs()
    apps = stats.applications(rows)
    first = min((d for d in (stats._day(j) for j in apps) if d), default=today)
    a = stats.analysis(rows, apps, first, today)
    wb = Workbook()
    wb.remove(wb.active)

    def app_row(j):
        vals = {"Role Family": stats.role_family(j.get("title")), "Salary": salary.label(j)}
        return [vals[h] if key is None else (j.get(key) or "") for h, key in APP_COLUMNS]
    _sheet(wb, "Applications", [h for h, _ in APP_COLUMNS],
           [app_row(j) for j in sorted(apps, key=lambda j: j.get("applied_on") or "", reverse=True)])
    for title, key, name in (("Companies", "companies", "Company"), ("Roles", "titles", "Role"), ("Role Families", "families", "Role family")):
        _sheet(wb, title, [name] + [h for h, _ in TABLE_COLUMNS], [[r["name"]] + [r[k] for _, k in TABLE_COLUMNS] for r in a[key]])
    _sheet(wb, "Statistics", ["Measure", "Count", "Mean", "Median", "Min", "Max", "Std dev", "25th percentile", "75th percentile"],
           [[m["label"], m["n"], m["mean"], m["median"], m["min"], m["max"], m["stdev"], m["p25"], m["p75"]] for m in a["metrics"]])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
