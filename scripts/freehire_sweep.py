#!/usr/bin/env python3
"""Step 1 of a run: sweep the freehire.me API with the queries in profile/settings.json,
drop closed / sensitive / wrong-language / stale / off-title / already-tracked postings,
and print the candidates. Standard library + curl.

  python3 scripts/freehire_sweep.py              # sweep, write runs/<today>/freehire.json
  python3 scripts/freehire_sweep.py --sample [N] # real titles for filter calibration (default 80)
  python3 scripts/freehire_sweep.py --detail N   # grep eligibility lines for candidate N (or all)
"""
import argparse, concurrent.futures as cf, datetime as dt, html, json, re, subprocess, sys, threading, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seekter  # noqa: E402

ROOT = seekter.ROOT
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
try:
    CFG = seekter.settings()
except ValueError as e:
    sys.exit(f"problem: profile/settings.json is not valid: {e}")
FH = CFG["freehire"]
UA = "Mozilla/5.0 seekter"
FAILURES = 0
REQUESTS = 0
FAIL_LOCK = threading.Lock()
FETCH = threading.local()
OUT = ROOT / "runs" / seekter.TODAY
ELIG = re.compile(r"[^.\n]*(based in|authori[sz]ed to work|eligible to work|residen|time ?zone|GMT|CET|UTC|"
                  r"countr|relocat|sponsor|visa|hybrid|office|remote|years)[^.\n]*", re.I)


def curl(url):
    FETCH.failed = False
    global FAILURES, REQUESTS
    with FAIL_LOCK:
        REQUESTS += 1
    r = subprocess.run(["curl", "--proto", "=https", "-sS", "-m", "40", "-A", UA, url], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        if r.returncode:
            raise ValueError("curl failed")
        return json.loads(r.stdout)
    except ValueError:
        FETCH.failed = True
        with FAIL_LOCK:
            FAILURES += 1
        return {}


def search(**kw):
    kw.setdefault("limit", 100)  # default ordering = relevance. Never sort by date (floods off-topic titles).
    global FAILURES
    response = curl("https://freehire.me/api/v1/jobs/search?" + urllib.parse.urlencode(kw))
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        if not FETCH.failed:
            with FAIL_LOCK:
                FAILURES += 1
        return []
    return data


def require(*keys):
    """Every search term is the candidate's, so an unfilled config stops the run
    instead of sweeping with a default that belongs to someone else's field."""
    missing = [k for k in keys if not (FH.get(k) if k in FH else CFG.get(k))]
    if missing:
        sys.exit("No freehire " + ", ".join(missing) + " in profile/settings.json" +
                 " yet. Run /seekter-init, or fill them in by hand (see templates/settings.json).")


def query_jobs():
    if not (FH.get("regions") or FH.get("home_country")):
        require("regions")
    jobs = [dict(q=q, category=c, work_mode="remote", regions=r)
            for q in FH["queries"] for c in FH["categories"] for r in FH["regions"]]
    if FH.get("home_country"):
        jobs += [dict(q=q, countries=FH["home_country"]) for q in FH["queries"]]
    return jobs


def harvest(jobs):
    rows = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for res in ex.map(lambda k: search(**k), jobs):
            for j in res:
                rows[j.get("public_slug") or j.get("slug")] = j
    return rows


def report_failures():
    if FAILURES:
        print(f"problem: {FAILURES} of {REQUESTS} freehire requests failed (network or bad JSON); the candidate list is incomplete")
        if FAILURES == REQUESTS:
            sys.exit(3)


def sample(limit):
    require("queries", "categories")
    rows = harvest(query_jobs())
    titles = {}
    for j in rows.values():
        title = seekter.clean_text(j.get("title") or "")
        if title and not j.get("closed_at"):
            titles.setdefault(title.casefold(), title)
    titles = list(titles.values())[:max(0, limit)]
    seekter._atomic_write(OUT / "sample-titles.txt", "\n".join(titles) + ("\n" if titles else ""))
    print(f"{len(titles)} real titles → runs/{seekter.TODAY}/sample-titles.txt")
    report_failures()


def location_text(j):
    parts = [j.get("location") or ""]
    for field in ("countries", "regions"):
        value = j.get(field) or []
        parts.extend(value if isinstance(value, list) else [value])
    return seekter.clean_text(" ".join(str(x) for x in parts))


def sweep():
    for key in ("blocked_companies", "location_flags"):
        CFG[key] = seekter._phrase_list(CFG, key)
    require("queries", "categories", "title_keep")
    jobs = query_jobs()
    keep, drop = seekter.title_filters(CFG)
    rows = harvest(jobs)
    blocked_domains = set(CFG.get("blocked_domains", ["gambling"]))
    langs = set(CFG.get("languages", ["en"]))
    window = int(FH.get("window_days", 3))
    tracked = {r.get("job_key") or seekter.job_key(r.get("url", "")) for r in seekter.all_apps()}
    now = dt.datetime.now(dt.timezone.utc)
    out, dropped = [], []
    stats = dict(raw=len(rows), closed=0, sector=0, language=0, title=0, company=0, stale=0, tracked=0)
    for s, j in rows.items():
        e = j.get("enrichment") or {}
        t = seekter.clean_text(j.get("title") or "")
        co = j.get("company")
        co = seekter.clean_text((co.get("name") if isinstance(co, dict) else co) or "")
        loc = location_text(j)
        url = seekter.clean_text(re.sub(r"[?&]utm_[^&]*", "", j.get("url") or ""))
        if "?" not in url:
            url = url.replace("&", "?", 1)
        verdict, reason = seekter.screen(t, co, loc, CFG, keep, drop)
        try:
            age = (now - dt.datetime.fromisoformat((j.get("posted_at") or j.get("created_at")).replace("Z", "+00:00"))).days
        except Exception:
            age = 99
        stage = ""
        if j.get("closed_at"):
            stage, reason = "closed", "closed"
        elif blocked_domains & set(e.get("domains") or []):
            stage, reason = "sector", "sector: " + ", ".join(sorted(blocked_domains & set(e.get("domains") or [])))
        elif e.get("posting_language") and e["posting_language"] not in langs:
            stage, reason = "language", "language: " + str(e["posting_language"])
        elif verdict == "skip":
            stage = "company" if reason.startswith("blocked company:") else "title"
        elif age > window:
            stage, reason = "stale", f"stale: {age} days"
        elif seekter.job_key(url) in tracked:
            stage, reason = "tracked", "tracked"
        if stage:
            stats[stage] += 1
            dropped.append(seekter.dropped_row(t, co, loc, url, reason))
            continue
        row = dict(slug=s, title=t, company=co, location=j.get("location"), countries=j.get("countries"),
                   regions=j.get("regions"), work_mode=j.get("work_mode"), age=age, url=url,
                   domains=e.get("domains"), reality=(j.get("reality") or {}).get("class"))
        if verdict == "flag":
            row["flag"] = reason
        out.append(clean_row(row))
    out.sort(key=lambda x: x["age"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "freehire.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "freehire-dropped.json").write_text(json.dumps(dropped, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats), f"→ {len(out)} candidates  (runs/{seekter.TODAY}/freehire.json)")
    print(f"dropped postings and their reasons: runs/{seekter.TODAY}/freehire-dropped.json")
    for i, o in enumerate(out):
        print(f"{i:>2} | {o['age']}d | {o['company']} | {o['title']} | {o['location']} | {o['url'][:80]}")
    report_failures()


def clean_row(value):
    if isinstance(value, dict):
        return {k: clean_row(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_row(v) for v in value]
    return seekter.clean_text(value) if isinstance(value, str) else value


def detail(which):
    path = OUT / "freehire.json"
    if not path.exists():
        sys.exit(f"problem: run the sweep first (no runs/{seekter.TODAY}/freehire.json)")
    cand = clean_row(json.loads(path.read_text(encoding="utf-8")))
    idx = list(range(len(cand))) if which == "all" else []
    if which != "all":
        for x in which.split(","):
            x = x.strip()
            if not re.fullmatch(r"[0-9]+", x) or int(x) >= len(cand):
                sys.exit(f"problem: no candidate {x} in runs/{seekter.TODAY}/freehire.json")
            idx.append(int(x))
    def fetch(i):
        result = curl("https://freehire.me/api/v1/jobs/" + urllib.parse.quote(cand[i]["slug"], safe=""))
        return result, FETCH.failed
    with cf.ThreadPoolExecutor(4) as ex:
        details = list(ex.map(fetch, idx))
    for i, (j, failed) in zip(idx, details):
        o = cand[i]
        j = j.get("data", j)
        d = html.unescape(re.sub(r"<[^>]+>", " ", j.get("description") or ""))
        print(f"===== {i} | {o['company']} | {o['title']} | {seekter.clean_text(j.get('countries'))} {seekter.clean_text(j.get('regions'))}\n{o['url']}")
        if failed:
            print("  - (fetch failed)")
            continue
        seen = []
        for m in ELIG.finditer(d):
            s = seekter.clean_text(m.group(0), 220)
            if s not in seen:
                seen.append(s)
                print("  -", s)
            if len(seen) >= 12:
                break

    failures = sum(failed for _, failed in details)
    if failures:
        print(f"problem: {failures} of {len(details)} detail requests failed; those entries show no eligibility lines")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", help="candidate index list (0,3,5) or 'all'")
    ap.add_argument("--sample", type=int, nargs="?", const=80, help="sample real titles before setting title filters (default 80)")
    a = ap.parse_args()
    if a.detail:
        detail(a.detail)
    elif a.sample is not None:
        sample(a.sample)
    else:
        sweep()
