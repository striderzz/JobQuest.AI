"""Fetch every source, then filter, enrich and de-duplicate."""
import concurrent.futures as cf
from . import filters, sources


def fetch_all(cfg, ctx, progress=None):
    """progress(done, total, postings) is called as each source finishes."""
    tasks = sources.plan(cfg, ctx)
    raw, status = [], {}
    with cf.ThreadPoolExecutor(12) as ex:
        futs = {ex.submit(fn): label for label, fn in tasks}
        for i, fu in enumerate(cf.as_completed(futs), 1):
            label = futs[fu]
            try:
                jobs = fu.result()
                raw += jobs
                status[label] = len(jobs)
            except Exception as e:                       # one dead board must not sink the run
                status[label] = f"error: {type(e).__name__}: {str(e)[:80]}"
            if progress:
                progress(i, len(tasks), len(raw))
    return raw, status


def select(raw, cfg, ctx, profiles, allow_unstated=False):
    """allow_unstated: keep postings that state no level at all (the AI judges those later)."""
    s = cfg["search"]
    now, days = ctx["now"], s["days"]
    stats = {"raw": len(raw)}

    first = []
    for j in raw:
        kind = filters.role_kind(j.title)
        if kind is None or kind not in s["roles"] or filters.title_is_senior(j.title):
            continue
        remote = filters.classify_location(j.location, j.title)
        if remote is None:
            continue
        if j.posted is None or (now - j.posted).days > days:
            continue
        j.kind, j.remote = kind, remote
        first.append(j)
    stats["after_title_location_date"] = len(first)

    needs = [j for j in first if j.detail]
    if needs:
        with cf.ThreadPoolExecutor(8) as ex:
            list(ex.map(_safe_detail, needs))

    best_degree = max((p.get("degree_level", 0) for p in profiles), default=0)
    kept = []
    for j in first:
        lv = filters.level_of(j, s["max_years"], allow_unstated, {"degree_level": best_degree})
        if lv is None:
            continue
        j.level, j.min_years = lv
        if j.level == "Intern" and not s["include_internships"]:
            continue
        kept.append(j)
    stats["after_level"] = len(kept)

    by_key = {}
    for j in sorted(kept, key=lambda j: sources.PRIORITY.get(j.source, 9)):
        j.key = filters.dedupe_key(j)
        if j.key in by_key:
            keep = by_key[j.key]
            if j.source != keep.source and j.source not in keep.also_on:
                keep.also_on.append(j.source)
            if len(j.description) > len(keep.description) and sources.PRIORITY.get(keep.source, 9) > 0:
                keep.description = j.description
            continue
        by_key[j.key] = j
    stats["unique"] = len(by_key)
    return list(by_key.values()), stats


def _safe_detail(job):
    try:
        job.detail(job)
    except Exception:
        pass
