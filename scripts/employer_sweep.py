#!/usr/bin/env python3
"""Step 3 of a run, the cheap part: read the job boards of the employers on the
candidate's watchlist straight from their public ATS APIs, and the latest Hacker News
"Who is hiring?" thread. No browser, no login, no aggregator in between.

  python3 scripts/employer_sweep.py          # watchlist from `employers` in profile/settings.json
  python3 scripts/employer_sweep.py --hn     # this month's HN "Who is hiring?" thread

Postings are filtered with the profile's title_keep / title_drop and deduped against the
tracker. What is left still needs its location line read before anything is filled.
"""
import argparse, datetime as dt, html, json, re, subprocess, sys, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seekter  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
try:
    CFG = seekter.settings()
except ValueError as e:
    sys.exit(f"problem: profile/settings.json is not valid: {e}")
UA = "Mozilla/5.0 seekter"
FAILURES = 0
REQUESTS = 0
FAIL_LOCK = threading.Lock()
OUT = seekter.ROOT / "runs" / seekter.TODAY
GAP = 1.0  # public APIs, but still one request after another (reference/sources/_core.md)


def curl(url):
    global FAILURES, REQUESTS
    with FAIL_LOCK:
        REQUESTS += 1
    r = subprocess.run(["curl", "--proto", "=https", "-sS", "-m", "30", "-A", UA, url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        if r.returncode:
            raise ValueError("curl failed")
        return json.loads(r.stdout)
    except ValueError:
        with FAIL_LOCK:
            FAILURES += 1
        return None


def greenhouse(slug):
    d = curl(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if not isinstance(d, dict) or "jobs" not in d:
        return None
    return [dict(title=j["title"], location=(j.get("location") or {}).get("name", ""),
                 url=f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}", **posted(j.get("updated_at"))) for j in d["jobs"]]


def ashby(slug):
    d = curl(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not isinstance(d, dict) or "jobs" not in d:
        return None
    out = []
    for j in d["jobs"]:
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        mode = j.get("workplaceType") or ""
        out.append(dict(title=j["title"], location=" / ".join(l for l in locs if l) + (f" ({mode})" if mode else ""),
                        url=j["jobUrl"], **posted(j.get("publishedAt"))))
    return out


def lever(slug):
    for host in ("api.lever.co", "api.eu.lever.co"):
        d = curl(f"https://{host}/v0/postings/{slug}?mode=json")
        if isinstance(d, list):
            return [dict(title=j["text"], location=(j.get("categories") or {}).get("location", ""),
                         url=j["hostedUrl"], **posted(j.get("createdAt"), milliseconds=True)) for j in d]
    return None


def workable(slug):
    d = curl(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    if not isinstance(d, dict) or "jobs" not in d:
        return None
    return [dict(title=j["title"], location=" ".join(x for x in (j.get("city"), j.get("country"),
                 "remote" if j.get("telecommuting") else "") if x),
                 url=f"https://apply.workable.com/{slug}/j/{j['shortcode']}", **posted(j.get("published_on") or j.get("created_at"))) for j in d["jobs"]]


def posted(value, milliseconds=False):
    if value is None or value == "":
        return {}
    try:
        date = dt.datetime.fromtimestamp(float(value) / 1000, dt.timezone.utc).date() if milliseconds else dt.date.fromisoformat(str(value)[:10])
        return dict(posted=date.isoformat())
    except (ValueError, TypeError, OverflowError, OSError):
        return {}


def smartrecruiters(slug):
    d = curl(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    if not isinstance(d, dict) or "content" not in d:
        return None
    out = []
    for j in d["content"]:
        loc = j.get("location") or {}
        out.append(dict(title=j["name"], location=" ".join(x for x in (loc.get("city"), loc.get("country"), "remote" if loc.get("remote") else "") if x),
                        url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}", **posted(j.get("releasedDate"))))
    return out


def recruitee(slug):
    d = curl(f"https://{slug}.recruitee.com/api/offers/")
    if not isinstance(d, dict) or "offers" not in d:
        return None
    return [dict(title=j["title"], location=" ".join(x for x in (j.get("location") or " ".join(x for x in (j.get("city"), j.get("country")) if x), "remote" if j.get("remote") else "") if x),
                 url=j["careers_url"], **posted(j.get("published_at"))) for j in d["offers"]]


ATS = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever, "workable": workable,
       "smartrecruiters": smartrecruiters, "recruitee": recruitee}


def employers():
    for key in ("blocked_companies", "location_flags"):
        CFG[key] = seekter._phrase_list(CFG, key)
    keep, drop = seekter.title_filters(CFG)
    tracked = {r.get("job_key") for r in seekter.all_apps()}
    watch = CFG.get("employers") or []
    if not watch:
        sys.exit("No employers in profile/settings.json yet. Add {name, ats, slug} rows "
                 "(see templates/settings.json and reference/sources/direct-employer.md).")
    out, problems, seen, dropped = [], [], 0, []
    for i, e in enumerate(watch):
        fetch = ATS.get(e.get("ats", ""))
        board_slug = e.get("slug", "")
        if not board_slug:
            problems.append(seekter.clean_text(f"{e.get('name')}: no slug"))
            continue
        if not isinstance(board_slug, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", board_slug):
            problems.append(seekter.clean_text(f'{e.get("name")}: slug "{board_slug}" has characters a board id never has'))
            continue
        jobs = fetch(board_slug) if fetch else None
        if jobs is None:
            problems.append(f"{e.get('name')}: no board at {e.get('ats')}:{e.get('slug')}")
        else:
            seen += len(jobs)
            for j in jobs:
                j = {k: seekter.clean_text(v) for k, v in j.items()}
                co = seekter.clean_text(e.get("name") or "")
                verdict, reason = seekter.screen(j["title"], co, j["location"], CFG, keep, drop)
                if verdict != "skip" and seekter.job_key(j["url"]) in tracked:
                    verdict, reason = "skip", "tracked"
                if verdict == "skip":
                    dropped.append(seekter.dropped_row(j["title"], co, j["location"], j["url"], reason))
                    continue
                if verdict == "flag":
                    j["flag"] = reason
                out.append(dict(company=co, **j))
        if i < len(watch) - 1:
            time.sleep(GAP)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "employers.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "employers-dropped.json").write_text(json.dumps(dropped, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(watch)} boards, {seen} postings → {len(out)} candidates  (runs/{seekter.TODAY}/employers.json)")
    for i, o in enumerate(out):
        print(f"{i:>2} | {o['company']} | {o['title']} | {o['location'][:60]} | {o['url']}")
    for p in problems:
        print("problem:", seekter.clean_text(p))


def clean(text):
    return seekter.clean_text(html.unescape(re.sub(r"<[^>]+>", " ", text or "")))


def hn():
    for key in ("blocked_companies", "location_flags"):
        CFG[key] = seekter._phrase_list(CFG, key)
    keep, drop = seekter.title_filters(CFG)
    hits = (curl("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=5")
            or {}).get("hits", [])
    story = next((h for h in hits if (h.get("title") or "").startswith("Ask HN: Who is hiring?")), None)
    if not story:
        sys.exit("problem: could not find this month's 'Who is hiring?' thread")
    thread = curl(f"https://hn.algolia.com/api/v1/items/{story['objectID']}") or {}
    if not thread:
        sys.exit("problem: could not fetch the hiring thread")
    posts = [c for c in thread.get("children") or [] if c.get("text")]
    out, dropped = [], []
    for c in posts:
        raw = html.unescape(c["text"])
        text = clean(raw)
        # The first line of an HN job post is its header: company | role | location | type.
        header = text[:200]
        co = header.split("|", 1)[0].strip() if "|" in header else ""
        url = seekter.clean_text(f"https://news.ycombinator.com/item?id={c['id']}")
        entry = seekter.blocked_company(co, CFG)
        match = drop.search(header)
        reason = "blocked company: " + entry if entry else ""
        if not reason:
            if not keep.search(text):
                reason = "title_keep: no match"
            elif match and not keep.search(header):
                reason = "title_drop: " + match.group(0)
        if reason:
            dropped.append(seekter.dropped_row(header, co, header, url, reason))
            continue
        links = re.findall(r'href="([^"]+)"', raw)
        row = dict(id=c["id"], header=header, remote=bool(re.search(r"\bremote\b", text, re.I)),
                        links=[seekter.clean_text(x) for x in links[:3]], url=url)
        flag = seekter.location_flag(header, CFG)
        if flag:
            row["flag"] = flag
        out.append(row)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hn.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "hn-dropped.json").write_text(json.dumps(dropped, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{seekter.clean_text(story['title'])}: {len(posts)} posts → {len(out)} mention the candidate's titles  (runs/{seekter.TODAY}/hn.json)")
    for i, o in enumerate(out):
        print(f"{i:>2} | {'R' if o['remote'] else '-'} | {o['header'][:150]} | {o['url']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hn", action="store_true", help="read this month's HN 'Who is hiring?' thread instead")
    a = ap.parse_args()
    hn() if a.hn else employers()
