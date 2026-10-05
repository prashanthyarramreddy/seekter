#!/usr/bin/env python3
"""Step 1 of a run: sweep the freehire.me API with the queries in profile/search.json,
drop closed / sensitive / wrong-language / stale / off-title / already-tracked postings,
and print the candidates. Standard library + curl.

  python3 scripts/freehire_sweep.py              # sweep, write runs/<today>/freehire.json
  python3 scripts/freehire_sweep.py --detail N   # grep eligibility lines for candidate N (or all)
"""
import argparse, concurrent.futures as cf, datetime as dt, html, json, re, subprocess, sys, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seekter  # noqa: E402

ROOT = seekter.ROOT
CFG = json.loads((ROOT / "profile" / "search.json").read_text(encoding="utf-8"))
FH = CFG["freehire"]
UA = "Mozilla/5.0 seekter"
OUT = ROOT / "runs" / seekter.TODAY
ELIG = re.compile(r"[^.\n]*(based in|authori[sz]ed to work|eligible to work|residen|time ?zone|GMT|CET|UTC|"
                  r"countr|relocat|sponsor|visa|hybrid|office|remote|years)[^.\n]*", re.I)


def curl(url):
    r = subprocess.run(["curl", "-sS", "-m", "40", "-A", UA, url], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def search(**kw):
    kw.setdefault("limit", 100)  # default ordering = relevance. Never sort by date (floods off-topic titles).
    return curl("https://freehire.me/api/v1/jobs/search?" + urllib.parse.urlencode(kw)).get("data", [])


def require(*keys):
    """Every search term is the candidate's, so an unfilled config stops the run
    instead of sweeping with a default that belongs to someone else's field."""
    missing = [k for k in keys if not (FH.get(k) if k in FH else CFG.get(k))]
    if missing:
        sys.exit("profile/search.json is missing " + ", ".join(missing) +
                 ". Run /seekter-init, or fill them in by hand (see templates/search.example.json).")


def sweep():
    require("queries", "categories", "regions", "title_keep")
    jobs = [dict(q=q, category=c, work_mode="remote", regions=r)
            for q in FH["queries"] for c in FH["categories"] for r in FH["regions"]]
    if FH.get("home_country"):
        jobs += [dict(q=q, countries=FH["home_country"]) for q in FH["queries"]]
    rows = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for res in ex.map(lambda k: search(**k), jobs):
            for j in res:
                rows[j.get("public_slug") or j.get("slug")] = j
    keep = re.compile(CFG["title_keep"], re.I)
    drop = re.compile(CFG["title_drop"] or r"(?!)", re.I)  # empty regex matches everything; (?!) matches nothing
    blocked_domains = set(CFG.get("blocked_domains", ["gambling"]))
    langs = set(CFG.get("languages", ["en"]))
    # window_hours is exact; window_days counts whole days (age.days, rounded down), so
    # window_days 2 still lets a 71-hour-old posting through. Hours win when both are set.
    window_h = float(FH["window_hours"]) if FH.get("window_hours") else None
    window = int(FH.get("window_days", 3))
    tracked = {r.get("job_key") for r in seekter.all_apps()}
    now = dt.datetime.now(dt.timezone.utc)
    out, stats = [], dict(raw=len(rows), closed=0, sector=0, language=0, title=0, stale=0, tracked=0)
    for s, j in rows.items():
        e = j.get("enrichment") or {}
        if j.get("closed_at"):
            stats["closed"] += 1; continue
        if blocked_domains & set(e.get("domains") or []):
            stats["sector"] += 1; continue
        if e.get("posting_language") and e["posting_language"] not in langs:
            stats["language"] += 1; continue
        t = j.get("title", "")
        if not keep.search(t) or drop.search(t):
            stats["title"] += 1; continue
        try:
            delta = now - dt.datetime.fromisoformat((j.get("posted_at") or j.get("created_at")).replace("Z", "+00:00"))
            age, age_h = delta.days, delta.total_seconds() / 3600
        except Exception:
            age, age_h = 99, 99 * 24.0
        if (age_h > window_h) if window_h is not None else (age > window):
            stats["stale"] += 1; continue
        url = re.sub(r"[?&]utm_[^&]*", "", j.get("url") or "")
        if "?" not in url:
            url = url.replace("&", "?", 1)
        if seekter.job_key(url) in tracked:
            stats["tracked"] += 1; continue
        co = j.get("company")
        out.append(dict(slug=s, title=t, company=co.get("name") if isinstance(co, dict) else co,
                        location=j.get("location"), countries=j.get("countries"), regions=j.get("regions"),
                        work_mode=j.get("work_mode"), age=age, url=url, domains=e.get("domains"),
                        reality=(j.get("reality") or {}).get("class")))
    out.sort(key=lambda x: x["age"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "freehire.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats), f"→ {len(out)} candidates  (runs/{seekter.TODAY}/freehire.json)")
    for i, o in enumerate(out):
        print(f"{i:>2} | {o['age']}d | {o['company']} | {o['title']} | {o['location']} | {o['url'][:80]}")


def detail(which):
    cand = json.loads((OUT / "freehire.json").read_text(encoding="utf-8"))
    idx = range(len(cand)) if which == "all" else [int(x) for x in which.split(",")]
    for i in idx:
        o = cand[i]
        j = curl("https://freehire.me/api/v1/jobs/" + o["slug"])
        j = j.get("data", j)
        d = html.unescape(re.sub(r"<[^>]+>", " ", j.get("description") or ""))
        print(f"===== {i} | {o['company']} | {o['title']} | {j.get('countries')} {j.get('regions')}\n{o['url']}")
        seen = []
        for m in ELIG.finditer(d):
            s = re.sub(r"\s+", " ", m.group(0)).strip()[:220]
            if s not in seen:
                seen.append(s)
                print("  -", s)
            if len(seen) >= 12:
                break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", help="candidate index list (0,3,5) or 'all'")
    a = ap.parse_args()
    detail(a.detail) if a.detail else sweep()
