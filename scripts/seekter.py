#!/usr/bin/env python3
"""Seekter tracker CLI. Standard library only.

Layout: applications/<YYYY-MM>/ holds one markdown file per posting (status in the front
matter; files never move) plus skipped.md, one table row per posting that was passed over.
applications/README.md and applications/<YYYY-MM>/README.md are generated views.

  python3 scripts/seekter.py check <url|linkedin-id> [--company NAME]   exit 0 new, 1 tracked, 2 hold (same company within the window)
  printf 'url | company\n4468710729\n' | python3 scripts/seekter.py check-many   (bare numbers = LinkedIn IDs)
  python3 scripts/seekter.py add --company X --role Y --status applied --url U [...]
  python3 scripts/seekter.py move <file|url> <status> [--note TEXT]
  python3 scripts/seekter.py list [--status S] [--since YYYY-MM-DD]
  python3 scripts/seekter.py filter-test [--titles FILE] [--date YYYY-MM-DD] [--keep REGEX] [--drop REGEX]
  python3 scripts/seekter.py queue [--date YYYY-MM-DD]
  python3 scripts/seekter.py index
  python3 scripts/seekter.py normalize [--dry-run]
  python3 scripts/seekter.py stats [--since YYYY-MM-DD]
  python3 scripts/seekter.py migrate [--keep-old]         (v1 status folders -> month folders)
"""
import argparse, datetime as dt, json, os, re, shutil, sys, tempfile, unicodedata
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "applications"
STATUSES = ["pending", "applied", "interviewing", "offer", "rejected", "closed", "skipped"]
FIELDS = ["company", "role", "status", "url", "source", "ats", "apply_type", "location_fit",
          "remote_scope", "fit", "posted", "applied", "updated", "job_key"]
TODAY = dt.date.today().isoformat()

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _clean_chars(s, body=False):
    s = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(s))
    return "".join(c for c in s if (body and c in "\n\t") or
                   not (ord(c) < 32 or 127 <= ord(c) <= 159 or
                        0x202a <= ord(c) <= 0x202e or 0x2066 <= ord(c) <= 0x2069))


def clean_text(s, n=None):
    s = re.sub(r"[\t\n\r\v\f\x85\u2028\u2029]", " ", str(s))
    return re.sub(r"\s+", " ", _clean_chars(s)).strip()[:n]


def title_filters(cfg):
    if not cfg.get("title_keep"):
        sys.exit("No title_keep in profile/settings.json yet. Run /seekter-init, or fill it in by hand.")
    try:
        return re.compile(cfg["title_keep"], re.I), re.compile(cfg.get("title_drop") or r"(?!)", re.I)
    except re.error as e:
        sys.exit(f"problem: profile/settings.json title_keep/title_drop is not a valid regex: {e}")


def title_ok(title, keep, drop):
    return bool(keep.search(title) and not drop.search(title))


def _phrase_list(cfg, key):
    value = cfg.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        sys.exit(f"problem: profile/settings.json {key} must be a list of names/phrases")
    return [x.strip() for x in value if x.strip()]


def blocked_company(company, cfg):
    return next((entry for entry in _phrase_list(cfg, "blocked_companies") if same_company(entry, company)), "")


def location_flag(location, cfg):
    return next(("location: " + phrase for phrase in _phrase_list(cfg, "location_flags")
                 if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", location, re.I)), "")


def screen(title, company, location, cfg, keep, drop):
    entry = blocked_company(company, cfg)
    if entry:
        return "skip", "blocked company: " + entry
    match = drop.search(title)
    if match:
        return "skip", "title_drop: " + match.group(0)
    if not keep.search(title):
        return "skip", "title_keep: no match"
    flag = location_flag(location, cfg)
    return ("flag", flag) if flag else ("consider", "")


def dropped_row(title, company, location, url, reason):
    return {k: clean_text(v or "") for k, v in zip(
        ("title", "company", "location", "url", "reason"), (title, company, location, url, reason))}


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, dir=path.parent, encoding="utf-8") as f:
            name = f.name
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


# ---------- job identity ----------
def job_key(url: str) -> str:
    """Stable identity for a posting, so the same job reached through different URLs matches."""
    if not url:
        return ""
    u = url.strip()
    p = urlparse(u if "://" in u else "https://" + u)
    host = p.netloc.lower().removeprefix("www.")
    q = parse_qs(p.query)
    path = p.path
    m = re.search(r"/jobs/view/(\d+)", path)
    if "linkedin.com" in host and m:
        return f"linkedin:{m.group(1)}"
    if "linkedin.com" in host and "currentJobId" in q:
        return f"linkedin:{q['currentJobId'][0]}"
    for k in ("gh_jid", "token"):
        if k in q and q[k][0].isdigit():
            return f"greenhouse:{q[k][0]}"
    if "ats_id" in q:
        return f"ats:{q['ats_id'][0].lower()}"
    # Same trap as Indeed below, but on company careers sites: the posting id lives in a
    # generic query param (?jobId=, ?gh_src=..., ?requisitionId=) and the path is identical
    # for every opening, so without this every Celonis/Workday-style posting collapses to
    # "<host>/job-detail" and the first one tracked makes all the others look like duplicates.
    # Measured 24 Sept: careers.celonis.com had two design roles that keyed identically.
    for k in ("jobid", "job_id", "requisitionid", "reqid", "posting_id", "postingid", "vacancyid"):
        for qk, qv in q.items():
            if qk.lower() == k and qv and re.fullmatch(r"\d{4,}", qv[0]):
                return f"{host}:{qv[0]}"
    # Hacker News "Who is hiring?" posts are comments, and every one lives at /item?id=<n>.
    # Without this the whole thread is one key. Measured 9 Oct: a skipped volunteer post
    # made the next post applied to from the same thread look like a duplicate.
    if host == "news.ycombinator.com" and q.get("id") and q["id"][0].isdigit():
        return f"hn:{q['id'][0]}"
    # Indeed keeps the posting id in the query string (?jk=, ?vjk= on a search page).
    # Without this, every posting on a domain collapses to "<host>/viewjob" and the
    # first one tracked makes all the others look like duplicates.
    if "indeed." in host:
        for k in ("jk", "vjk"):
            if q.get(k):
                return f"indeed:{q[k][0].lower()}"
    # Breezy slugs are "<position_id>-<title-slug>", and the employer can rename a posting
    # without opening a new one. Keying on the whole path makes a rename look like a brand
    # new job. Measured 25 Sept: Cal.com's ff94f3182ac2 was applied to on 18 Sept as
    # "senior-product-designer", was re-read today as "senior-product-design-engineer",
    # passed dedup as NEW, and only Breezy's own server caught it ("It looks like maybe
    # you've already applied to this job?") after the form was filled. Key on the id alone.
    if "breezy.hr" in host:
        m = re.search(r"/p/([0-9a-f]{8,})", path, re.I)
        if m:
            return f"breezy:{m.group(1).lower()}"
    m = re.search(UUID, path, re.I)
    if m:
        return f"uuid:{m.group(0).lower()}"
    if "greenhouse" in host:
        m = re.search(r"/jobs/(\d+)", path)
        if m:
            return f"greenhouse:{m.group(1)}"
    m = re.search(r"/(\d{7,})(?:[-/]|$)", path)
    if m:
        return f"{host}:{m.group(1)}"
    path = re.sub(r"/(application|apply|apply/)?$", "", path.rstrip("/"))
    # A URL with no path would key on the host alone, which makes every posting on
    # that site one record. Fall back to the fragment, then to the query.
    if not path:
        tail = p.fragment or p.query
        if tail:
            return f"{host}#{tail}".lower()
    return f"{host}{path}".lower()


ATS_HOSTS = [
    ("ashbyhq.com", "ashby"), ("greenhouse.io", "greenhouse"), ("lever.co", "lever"),
    ("myworkdayjobs.com", "workday"), ("workable.com", "workable"), ("recruitee.com", "recruitee"),
    ("personio.", "personio"), ("teamtailor.com", "teamtailor"), ("smartrecruiters.com", "smartrecruiters"),
    ("bamboohr.com", "bamboohr"), ("pinpointhq.com", "pinpoint"), ("breezy.hr", "breezy"),
    ("rippling.com", "rippling"), ("icims.com", "icims"), ("taleo.net", "taleo"), ("join.com", "join"),
    ("homerun.co", "homerun"), ("hire.trakstar.com", "trakstar"), ("jobylon.com", "jobylon"),
    ("hr-on.com", "hr-on"), ("dayforcehcm.com", "dayforce"), ("successfactors", "successfactors"),
    ("docs.google.com/forms", "google-forms"), ("forms.gle", "google-forms"),
    ("linkedin.com", "linkedin"), ("djinni.co", "djinni"),
]
# Values that are an application system, not a place the posting was found.
ATS_NAMES = {v for _, v in ATS_HOSTS} - {"linkedin", "djinni"}


def ats_from_url(url: str) -> str:
    u = (url or "").lower()
    for host, name in ATS_HOSTS:
        if host in u:
            return name
    return ""


def norm(v: str) -> str:
    """'Company site' -> 'company-site', 'Other board' -> 'other-board'."""
    return re.sub(r"[^a-z0-9]+", "-", (v or "").strip().lower()).strip("-")


# NFKD folds a letter to ASCII only when it decomposes into a base letter plus a
# combining mark. A letter that is its own base character has no decomposition, so
# `encode("ascii", "ignore")` deletes it outright: dotless ı, Polish ł, Nordic ø/æ,
# Croatian đ, German ß and Icelandic þ/ð all vanish, so an employer name
# reached a filename with a letter missing ("desgn" for "desıgn"). `slug` is also what the
# same-company check compares, so two different employers could fold onto one
# string: "Lıght" and "Lght" both became "lght".
FOLD = str.maketrans({"ı": "i", "İ": "I", "ł": "l", "Ł": "L", "ø": "o", "Ø": "O",
                      "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "ß": "ss",
                      "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "TH"})


def slug(s: str, n: int = 40) -> str:
    s = unicodedata.normalize("NFKD", (s or "").translate(FOLD)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:n].rstrip("-") or "x"


# ---------- storage ----------
# applications/<YYYY-MM>/<date>--<company>--<role>.md   one file per posting that isn't a skip;
#                                                      status lives in the front matter, files never move
# applications/<YYYY-MM>/skipped.md                    one table row per skipped posting
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
SKIP_COLS = ["Date", "Company", "Role", "Reason", "Source", "Link", "Key"]


def month_of(date: str) -> str:
    return (date or TODAY)[:7]


def month_dirs():
    if not APPS.is_dir():
        return []
    return sorted(d for d in APPS.iterdir() if d.is_dir() and MONTH_RE.match(d.name))


def read(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 4:].lstrip("\n")
    meta["_path"] = path
    meta["_body"] = body
    meta["_kind"] = "file"
    return meta


def write(path: Path, meta: dict, body: str) -> None:
    lines = ["---"]
    for k in FIELDS:
        if meta.get(k) not in (None, ""):
            lines.append(f"{k}: {str(meta[k]).replace(chr(10), ' ')}")
    for k, v in meta.items():
        if k not in FIELDS and not k.startswith("_") and v not in (None, ""):
            lines.append(f"{k}: {str(v).replace(chr(10), ' ')}")
    lines.append("---")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, "\n".join(lines) + "\n\n" + body.rstrip() + "\n")


def _cell(v: str) -> str:
    """One table cell. Idempotent: an already-escaped pipe is not escaped again,
    otherwise a value round-tripping through the table grows a backslash each pass."""
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return s.replace("\\|", "|").replace("|", "\\|")


def _md_text(s):
    text = re.sub(r"\\(?!\|)", lambda m: "\\\\", _cell(s))
    return text.replace("[", r"\[").replace("]", r"\]").replace("<", "&lt;").replace(">", "&gt;")


def _md_url(url):
    if not url.startswith(("http://", "https://")):
        return ""
    return quote(url, safe=":/?#@!$&'*+,;=%-._~")


def _split_row(line: str):
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [p.strip().replace("\\|", "|") for p in parts]


def read_skips(path: Path):
    out = []
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.startswith("| ") or line.startswith("| Date |"):
            continue
        cells = _split_row(line)
        if len(cells) < len(SKIP_COLS):
            continue
        d = dict(zip(SKIP_COLS, cells))
        url = d["Link"].strip("<>")
        # Decode only when display escaping changed the stored identity; an already
        # percent-encoded posting URL must keep the key it originally had.
        decoded = unquote(url)
        key = d["Key"].replace("&#91;", "[").replace("&#93;", "]")
        if d["Key"] not in (job_key(url), job_key(decoded)) and key in (job_key(url), job_key(decoded)):
            d["Key"] = key
        if d["Key"] and job_key(url) != d["Key"] and job_key(decoded) == d["Key"]:
            url = decoded
        # `ats` has no column of its own: it is a pure function of the URL, so it is
        # derived on read. Without it every row would look unnormalised on every pass.
        out.append(dict(updated=d["Date"], company=d["Company"], role=d["Role"], notes=d["Reason"],
                        source=d["Source"], url=url, job_key=d["Key"] or job_key(url), status="skipped",
                        ats=ats_from_url(url), _path=path, _kind="row", _line=n, _body=""))
    return out


def write_skips(path: Path, recs) -> None:
    recs = sorted(recs, key=lambda r: (r.get("updated", ""), r.get("company", "").lower()))
    lines = [f"# Skipped · {path.parent.name}", "",
             "Postings looked at and not applied to, with the reason. Written by `scripts/seekter.py`; "
             "dedup reads the Key column, so a posting here is never evaluated twice.", "",
             "| " + " | ".join(SKIP_COLS) + " |", "|" + "---|" * len(SKIP_COLS)]
    for r in recs:
        url = clean_text(r.get("url") or "")
        encoded = _md_url(url)
        link = f"<{encoded}>" if encoded else url
        lines.append("| " + " | ".join(_cell(x) for x in (
            r.get("updated", ""), r.get("company", ""), r.get("role", ""), r.get("notes", ""),
            r.get("source", ""), link, r.get("job_key", "").replace("[", "&#91;").replace("]", "&#93;"))) + " |")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, "\n".join(lines) + "\n")


def all_apps():
    for d in month_dirs():
        for f in sorted(d.glob("*.md")):
            if f.name in ("skipped.md", "README.md"):
                continue
            yield read(f)
        yield from read_skips(d / "skipped.md")


def label(r) -> str:
    p = r["_path"].relative_to(ROOT).as_posix()
    return p + (f" (row: {r.get('company')})" if r["_kind"] == "row" else "")


def find(target: str):
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p) if (Path.cwd() / p).exists() else (ROOT / p)
    if p.exists():
        resolved = p.resolve()
        if not (resolved.is_file() and resolved.suffix == ".md" and
                resolved.name not in ("README.md", "skipped.md") and
                MONTH_RE.fullmatch(resolved.parent.name) and resolved.parent.parent == APPS.resolve()):
            sys.exit(f"refusing: {target} is not a tracker record under applications/")
        return read(resolved)
    key = job_key(target)
    for a in all_apps():
        if (a.get("job_key") or job_key(a.get("url", ""))) == key:
            return a
    return None


def new_file_path(date: str, company: str, role: str) -> Path:
    name = f"{date}--{slug(company, 30)}--{slug(role, 40)}.md"
    path = APPS / month_of(date) / name
    i = 2
    while path.exists():
        path = path.with_name(name.replace(".md", f"-{i}.md"))
        i += 1
    return path


def body_for(role, company, why="", notes="", answers="", log=""):
    return (f"# {role} · {company}\n\n## Why it fits\n\n{why}\n\n## Notes\n\n{notes}\n\n"
            f"## Answers submitted\n\n{answers}\n\n## Log\n\n{log}\n")


def save(meta: dict, body: str = "", log: str = ""):
    """Store a new record in the right place for its status. Returns the path written."""
    fix_meta(meta)
    date = meta.get("applied") or meta.get("updated") or TODAY
    if meta["status"] == "skipped":
        path = APPS / month_of(date) / "skipped.md"
        recs = read_skips(path)
        recs.append(dict(meta, updated=meta.get("updated") or date))
        write_skips(path, recs)
        return path
    path = new_file_path(date, meta["company"], meta["role"])
    write(path, meta, body or body_for(meta["role"], meta["company"], log=log))
    return path


def remove(r) -> None:
    if r["_kind"] == "row":
        keep = [x for x in read_skips(r["_path"]) if x["_line"] != r["_line"]]
        write_skips(r["_path"], keep)
    else:
        r["_path"].unlink()


def append_log(body: str, line: str) -> str:
    """Add a log line, once. Running the same `move` twice (a re-run script, a retried
    batch) used to write the identical line twice; measured 6 Oct on 27 records."""
    if "## Log" not in body:
        body += "\n## Log\n"
    lines = [x for x in body.rstrip().splitlines() if x.strip()]
    if lines and lines[-1].strip() == line.strip():
        return body.rstrip() + "\n"
    return body.rstrip() + "\n" + line + "\n"


def dedupe_log(body: str) -> str:
    """Collapse adjacent identical lines in the Log section."""
    m = re.search(r"(## Log\n)(.*?)(?=\n## |\Z)", body, re.S)
    if not m:
        return body
    out = []
    for x in m.group(2).splitlines(keepends=True):
        if x.strip() and out and x.strip() == out[-1].strip():
            continue
        out.append(x)
    return body[:m.start(2)] + "".join(out) + body[m.end(2):]


def add_answers(body: str, text: str) -> str:
    """Append submitted answers to the record's Answers section.

    A form often gets its free text after the record exists: the record is opened as a
    hand-off, the user supplies the missing facts, and the form is submitted later.
    Without this the answers could only be added by editing the file by hand, which the
    tracker forbids (measured 6 Oct, Secfix)."""
    text = (text or "").strip()
    if not text:
        return body
    m = re.search(r"(## Answers submitted\n)(.*?)(?=\n## |\Z)", body, re.S)
    if not m:
        cut = body.find("## Log")
        block = f"## Answers submitted\n\n{text}\n\n"
        return body[:cut] + block + body[cut:] if cut >= 0 else body.rstrip() + "\n\n" + block
    old = m.group(2).strip()
    if text in old:
        return body
    new = f"\n{old}\n\n{text}\n" if old else f"\n{text}\n"
    return body[:m.start(2)] + new + body[m.end(2):]


def section(body: str, name: str) -> str:
    m = re.search(rf"## {re.escape(name)}\n(.*?)(?=\n## |\Z)", body, re.S)
    return m.group(1).strip() if m else ""


SENT = ("applied", "interviewing", "offer", "rejected", "closed")


def keep_as_file(r) -> str:
    """Why this record must not be collapsed into a skip row, or '' if it may be.

    A skip row holds a date, a company, a role and one line of reason. That is
    the whole record for a posting that was only ever looked at. For a posting
    that was applied to, it would throw away the submitted answers, the log and
    the application date -- so those stay files, with `status: skipped`.
    """
    if r["_kind"] != "file":
        return ""
    if section(r.get("_body", ""), "Answers submitted"):
        return "it records submitted answers"
    if r.get("applied"):
        return f"it was applied to on {r['applied']}"
    if r.get("status") in SENT:
        return f"its status is '{r['status']}'"
    return ""


# ---------- normalisation ----------
def fix_meta(r: dict) -> dict:
    """Normalise one record: lowercase-hyphen enums, ATS derived from the URL,
    and an ATS name misfiled as `source` moved to `ats`."""
    before = {k: r.get(k, "") for k in ("source", "ats", "apply_type", "job_key")}
    src, ats = norm(r.get("source")), norm(r.get("ats"))
    if src == "other-board":
        src = "board"
    if src in ATS_NAMES:
        ats = ats or src
        src = "board"  # the tracker only knew the form system, not where the posting was found
    r["source"] = src
    r["ats"] = ats or ats_from_url(r.get("url", ""))
    r["apply_type"] = norm(r.get("apply_type"))
    if r.get("url") and not r.get("job_key"):
        r["job_key"] = job_key(r["url"])
    return {k: (before[k], r.get(k, "")) for k in before if before[k] != r.get(k, "")}


# ---------- one application per company ----------
# Greenhouse lets an employer auto-reject a candidate's further applications to
# a department within a window, or after a rejection, and the candidate is told
# only if the employer switches that on ("blocked by auto reject rule"). So a
# second role at the same company inside the window can be a silent loss that
# also looks like spam. Measured 2 Oct: two roles at one company and a second
# role at another went out the same afternoon. The window is the candidate's
# (`same_company_days` in profile/settings.json), 30 days by default.
HOLDING = ("pending", "applied", "rejected")   # within the window
ALWAYS_HOLDING = ("interviewing", "offer")      # a live process holds regardless of date


# ---------- settings ----------
# One file holds every switch and number: profile/settings.json, over the defaults in
# templates/settings.json. A key the user never set falls back to the template, so a
# half-finished /seekter-init still runs. profile/search.json is the old name; it is
# moved, not rewritten, the first time anything reads the settings.
SETTINGS = ROOT / "profile" / "settings.json"
LEGACY_SETTINGS = ROOT / "profile" / "search.json"
SETTINGS_TEMPLATE = ROOT / "templates" / "settings.json"


def _merge(defaults, mine):
    out = dict(defaults)
    for k, v in mine.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _defaults() -> dict:
    try:
        return json.loads(SETTINGS_TEMPLATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def settings(create: bool = False, read_only: bool = False) -> dict:
    """The user's settings over the template's defaults.

    With create=True a missing profile/settings.json is copied from the template, which is
    what /seekter-run does at start so the user has a file to edit."""
    source = SETTINGS
    if read_only and not SETTINGS.exists() and LEGACY_SETTINGS.exists():
        source = LEGACY_SETTINGS
    elif not SETTINGS.exists() and LEGACY_SETTINGS.exists():
        LEGACY_SETTINGS.rename(SETTINGS)
        print("moved profile/search.json to profile/settings.json (values unchanged)", file=sys.stderr)
    elif SETTINGS.exists() and LEGACY_SETTINGS.exists():
        print("warning: both profile/settings.json and profile/search.json exist; only settings.json is read."
              " Move any value you still need from search.json, then delete it.", file=sys.stderr)
    if not source.exists():
        if create and not read_only and SETTINGS_TEMPLATE.exists():
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SETTINGS_TEMPLATE, SETTINGS)
            print("created profile/settings.json from the template; every value is a default", file=sys.stderr)
        return _defaults()
    mine = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(mine, dict):
        raise ValueError("the top level must be an object { ... }")
    for k in ("linkedin", "freehire"):
        if k in mine and not isinstance(mine[k], dict):
            raise ValueError(f'"{k}" must be an object {{ ... }}')
    return _merge(_defaults(), mine)


def same_company_days() -> int:
    try:
        return int(settings().get("same_company_days", 30))
    except (OSError, ValueError, TypeError) as e:
        # Never silently: a broken file would otherwise turn a 90-day window into 30.
        print(f"warning: profile/settings.json could not be read ({e}); using 30 days", file=sys.stderr)
        return 30


def same_company(query: str, company: str) -> bool:
    """`query` names the employer in `company`: the same name, or whole words of it.

    A substring is not enough. Measured 5 and 6 Oct: "telli" matched Intellias and
    Intermedia Intelligent, and "Flex" matched WorkFlex and Engiflex, so both were
    held under the 30-day rule for companies they had never applied to. A held
    posting is skipped, so a false match costs an application, not a line of output.
    """
    return _same_company_slug(query, slug(company, 80))


def _same_company_slug(query, c):
    q = slug(query, 80)
    if not (query or "").strip() or q == "x":
        return False
    return q == c or re.search(rf"(?:^|-){re.escape(q)}(?:-|$)", c) is not None


def holding(records, days):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return [r for r in records
            if r.get("status") in ALWAYS_HOLDING
            or (r.get("status") in HOLDING and (r.get("applied") or r.get("updated") or "") >= since)]


def bare_id_to_url(url: str) -> str:
    """A bare number is a LinkedIn job id, in `check` as in `check-many`."""
    url = url.strip()
    return f"https://www.linkedin.com/jobs/view/{url}/" if url.isdigit() else url


# ---------- commands ----------
def cmd_check(a):
    key = job_key(bare_id_to_url(a.url))
    same, company = [], []
    for r in all_apps():
        if key and (r.get("job_key") == key or job_key(r.get("url", "")) == key):
            same.append(r)
        elif a.company and same_company(a.company, r.get("company", "")):
            company.append(r)
    if same:
        print("DUPLICATE: this posting is already tracked")
        for r in same:
            print(f"  {r.get('status')} | {r.get('company')} | {r.get('role')} | {label(r)}")
        sys.exit(1)
    print(f"NEW  key={key}")
    for r in company:
        print(f"  same company, other role: {r.get('status')} | {r.get('role')} | {r.get('applied') or r.get('updated')}")
    days = same_company_days()
    held = holding(company, days)
    if held:
        print(f"HOLD: one application per company per {days} days. Apply only if the user says so;"
              f" otherwise skip with this reason, or pick the better-fitting role while neither has gone out.")
        sys.exit(2)


def cmd_check_many(a):
    """stdin: one posting per line, 'url' or 'url | company'. Prints NEW / DUP / SAMECO / HOLD per line."""
    apps = list(all_apps())
    keys = {r.get("job_key") or job_key(r.get("url", "")): r for r in apps}
    days = same_company_days()
    companies = [(r, slug(r.get("company", ""), 80)) for r in apps]
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        url, _, co = (x.strip() for x in line.partition("|"))
        url = bare_id_to_url(url)
        r = keys.get(job_key(url))
        if r:
            print(f"DUP    {line} -> {r.get('status')} {label(r)}")
            continue
        same = [r for r, c in companies if co and _same_company_slug(co, c)]
        tag = "HOLD   " if holding(same, days) else "SAMECO " if same else "NEW    "
        print(tag + line + (f" -> {len(same)} earlier: " + ", ".join(f"{x.get('status')}:{x.get('role')}" for x in same[:3]) if same else ""))


def _run_date(value):
    date = value or TODAY
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError("invalid date")
        dt.date.fromisoformat(date)
    except ValueError:
        sys.exit("problem: --date must be YYYY-MM-DD")
    return date


def cmd_filter_test(a):
    date = _run_date(a.date)
    titles = []
    try:
        if a.titles:
            titles = sys.stdin.read().splitlines() if a.titles == "-" else Path(a.titles).read_text(encoding="utf-8").splitlines()
        else:
            directory = ROOT / "runs" / date
            if (directory / "hn.json").exists():
                print("(HN posts are matched on the whole post by the sweep, so they are not part of this test)")
            for name in ("freehire", "employers"):
                for suffix in (".json", "-dropped.json"):
                    path = directory / (name + suffix)
                    if path.exists():
                        for row in json.loads(path.read_text(encoding="utf-8")):
                            if suffix == "-dropped.json" and not row.get("reason", "").startswith("title_"):
                                continue
                            titles.append(row.get("header", row.get("title", "")))
    except (OSError, ValueError, TypeError, AttributeError) as e:
        sys.exit(f"problem: could not read titles: {clean_text(e)}")
    unique = {}
    for title in titles:
        title = clean_text(title or "")
        if title:
            unique.setdefault(title.casefold(), title)
    if not unique:
        print("problem: no titles to test (run a sweep, or freehire_sweep.py --sample)")
        sys.exit(1)
    try:
        cfg = settings(read_only=True)
        if a.keep is not None:
            cfg["title_keep"] = a.keep
        if a.drop is not None:
            cfg["title_drop"] = a.drop
        keep, drop = title_filters(cfg)
    except ValueError as e:
        sys.exit(f"problem: profile/settings.json is not valid: {e}")
    except SystemExit as e:
        print(str(e))
        sys.exit(1)
    considered, skipped = [], []
    for title in sorted(unique.values(), key=str.casefold):
        match = drop.search(title)
        if match or not keep.search(title):
            skipped.append((title, match.group(0) if match else "no keep match"))
        else:
            considered.append(title)
    print(f"WOULD CONSIDER ({len(considered)})")
    for title in considered:
        print(f"  ✓ {title}")
    print(f"WOULD SKIP ({len(skipped)})")
    for title, reason in skipped:
        print(f"  ✗ {title}   ← {reason}")


def cmd_queue(a):
    date = _run_date(a.date)
    outdir = ROOT / "runs" / date
    sources = [("freehire", "freehire.json"), ("employer", "employers.json"), ("hn", "hn.json")]
    if not any((outdir / name).exists() for _, name in sources):
        print(f"problem: no sweep output in runs/{date}/ (run the sweeps first)")
        sys.exit(1)
    try:
        cfg = settings()
        keep, drop = title_filters(cfg)
    except ValueError as e:
        sys.exit(f"problem: profile/settings.json is not valid: {e}")
    apps = list(all_apps())
    seen = {r.get("job_key") or job_key(r.get("url", "")) for r in apps}
    companies = [(r, slug(r.get("company", ""), 80)) for r in apps]
    days = same_company_days()
    out, dropped, blocked = [], 0, 0
    for source, name in sources:
        path = outdir / name
        if not path.exists():
            continue
        try:
            sweep_rows = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            print(f"problem: runs/{date}/{name} is not valid JSON")
            sys.exit(1)
        if not isinstance(sweep_rows, list) or any(not isinstance(row, dict) for row in sweep_rows):
            print(f"problem: runs/{date}/{name} is not valid sweep output (expected a list of objects)")
            sys.exit(1)
        for row in sweep_rows:
            title = clean_text(row.get("header" if source == "hn" else "title") or "")
            company = clean_text(row.get("company") or "") if source != "hn" else (title.split("|", 1)[0].strip() if "|" in title else "")
            url = clean_text(row.get("url") or "")
            key = job_key(url)
            if blocked_company(company, cfg):
                blocked += 1
                continue
            location = clean_text(row.get("location") or "")
            flag = clean_text(row.get("flag") or location_flag(title if source == "hn" else location, cfg))
            if source == "hn":
                if drop.search(title) and not keep.search(title):
                    continue
            elif not title_ok(title, keep, drop):
                continue
            if key:
                if key in seen:
                    dropped += 1
                    continue
                seen.add(key)
            same = [r for r, c in companies if company and _same_company_slug(company, c)]
            status = "HOLD" if holding(same, days) else "SAMECO" if same else "NEW"
            item = dict(source=source, company=company, title=title, location=location, flag=flag, url=url, status=status)
            for field in ("age", "posted"):
                if field in row:
                    item[field] = row[field]
            out.append(item)
    def order(r):
        freshness = float("inf")
        try:
            freshness = float(r["age"]) if "age" in r else (dt.date.fromisoformat(date) - dt.date.fromisoformat(r["posted"])).days
        except (KeyError, ValueError, TypeError):
            pass
        return ("NEW SAMECO HOLD".split().index(r["status"]), bool(r["flag"]), freshness)
    out.sort(key=order)
    lines = ["The hold column and order are hints; every posting is still opened and checked with `seekter.py check` before a form (run skill §2.5). "
             "HN rows were title-matched on the whole post by the sweep, so the queue only applies title_drop to their header.", "",
             "| # | Status | Source | Company | Role | Location | Flag | Apply URL |", "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(out, 1):
        cells = [_cell(clean_text(x)) for x in (i, r["status"], r["source"], r["company"], r["title"], r["location"], r["flag"],
                 _md_url(r["url"]))]
        for column in (3, 4, 5, 6):
            cells[column] = _md_text(cells[column])
        lines.append("| " + " | ".join(cells) + " |")
    queue = outdir / "queue.md"
    hand = ("## Added by hand\n\nLinkedIn, board and pasted-link candidates go here; "
            "`seekter.py queue` keeps this section when it rebuilds the table above.\n")
    if queue.exists():
        with queue.open(encoding="utf-8", newline="") as f:
            old = f.read()
        heading = re.search(r"^## Added by hand(?:\r?\n|$)", old, re.M)
        if heading:
            hand = old[heading.start():]
    _atomic_write(queue, "\n".join(lines) + "\n\n" + hand)
    counts = {s: sum(r["status"] == s for r in out) for s in ("NEW", "SAMECO", "HOLD")}
    print(f"queue: {len(out)} postings ({counts['NEW']} new, {counts['SAMECO']} same company, {counts['HOLD']} on hold), {dropped} already tracked or duplicate, {sum(bool(r['flag']) for r in out)} flagged, {blocked} blocked company → runs/{date}/queue.md")


def cmd_add(a):
    for field in ("company", "role", "url", "source", "remote_scope", "fit", "location_fit"):
        setattr(a, field, clean_text(getattr(a, field)))
    for field in ("why", "notes", "answers", "log"):
        setattr(a, field, _clean_chars(getattr(a, field), body=True))
    if a.status not in STATUSES:
        sys.exit(f"status must be one of {STATUSES}")
    key = job_key(a.url or "")
    if key and not a.force:
        for r in all_apps():
            if (r.get("job_key") or job_key(r.get("url", ""))) == key:
                sys.exit(f"DUPLICATE: {label(r)} (use --force to add anyway)")
    today = a.date or TODAY
    meta = dict(company=a.company, role=a.role, status=a.status, url=a.url, source=a.source, ats=a.ats,
                apply_type=a.apply_type, location_fit=a.location_fit, remote_scope=a.remote_scope, fit=a.fit,
                posted=a.posted, applied=a.applied or (today if a.status == "applied" else ""),
                updated=today, job_key=key)
    if a.status == "skipped":
        meta["notes"] = a.notes or a.why
        path = save(meta)
    else:
        log = f"- {today}: {a.status}" + (f". {a.log}" if a.log else "")
        path = save(meta, body_for(a.role, a.company, a.why, a.notes, a.answers, log))
    print(path.relative_to(ROOT))
    if not a.no_index:
        cmd_index(None, quiet=True)


def cmd_move(a):
    if a.status not in STATUSES:
        sys.exit(f"status must be one of {STATUSES}")
    r = find(a.target)
    if not r:
        sys.exit("not found")
    a.note = _clean_chars(a.note, body=True)
    note = f"- {TODAY}: {a.status}" + (f". {a.note}" if a.note else "")
    if r["_kind"] == "row" and a.status == "skipped":
        if a.answers:
            print("a skip row has no answers section; answers were not stored", file=sys.stderr)
        if not a.note:
            print("already skipped: " + label(r)); return
        # Re-annotating a skip is the only way to correct a reason that turned out to be
        # wrong, because the tracker is CLI-only and hand-editing the table is forbidden.
        # Keep the original reason and append the correction after it, dated, so the
        # record shows both what was decided and why it changed.
        remove(r)
        meta = {k: v for k, v in r.items() if not k.startswith("_")}
        meta.update(status="skipped", updated=TODAY,
                    notes=" ".join(x for x in (r.get("notes", ""), f"[{TODAY}] {a.note}") if x))
        path = save(meta)
        print(path.relative_to(ROOT))
        cmd_index(None, quiet=True)
        return
    if r["_kind"] == "row":
        # a skip turned into something else: it gets its own file
        remove(r)
        meta = {k: v for k, v in r.items() if not k.startswith("_")}
        reason = meta.pop("notes", "")
        meta.update(status=a.status, updated=TODAY)
        if a.status == "applied":
            meta["applied"] = TODAY
        path = save(meta, body_for(meta["role"], meta["company"], notes=f"Skipped earlier: {reason}",
                                   answers=a.answers, log=f"- {r.get('updated')}: skipped\n{note}"))
    elif a.status == "skipped":
        meta = {k: v for k, v in r.items() if not k.startswith("_")}
        keep = keep_as_file(r)
        if keep:
            # Collapsing this to a table row would drop the answers, the reasoning
            # and the dates, and `--answers` is what makes "no sentence twice"
            # checkable. A skip that was once a real application stays a file.
            body = add_answers(r["_body"], a.answers)
            meta.update(status="skipped", updated=TODAY)
            write(r["_path"], meta, append_log(body, note))
            path = r["_path"]
            print(f"kept as a file rather than a skip row: {keep}", file=sys.stderr)
        else:
            remove(r)
            meta.update(status="skipped", updated=TODAY,
                        notes=" ".join(x for x in (a.note, section(r["_body"], "Notes")) if x))
            path = save(meta)
    else:
        path, body = r["_path"], r["_body"]
        meta = {k: v for k, v in r.items() if not k.startswith("_")}
        meta.update(status=a.status, updated=TODAY)
        if a.status == "applied" and not meta.get("applied"):
            meta["applied"] = TODAY
        body = add_answers(body, a.answers)
        write(path, meta, append_log(body, note))
    print(path.relative_to(ROOT))
    cmd_index(None, quiet=True)


# Values that can't have a default: without them no form can be filled honestly.
# Everything else still `{{...}}` in the profile is simply unknown, and the run asks.
REQUIRED_PROFILE = {"EMAIL": "application email", "CV_DEFAULT": "default CV",
                    "COUNTRY": "country you live and work in"}


def _defaulted(defaults, mine, prefix=""):
    """Settings left out of the user's file or still equal to the template's value."""
    out = []
    for k, v in defaults.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(mine.get(k), dict):
            out += _defaulted(v, mine[k], prefix + k + ".")
        elif k not in mine or mine[k] == v:
            out.append(prefix + k)
    return out


def cmd_settings(a):
    try:
        cfg = settings(create=True)
    except ValueError as e:
        sys.exit(f"problem: profile/settings.json is not valid: {e}")
    if not a.check:
        def clean(d):
            return {k: clean(v) if isinstance(v, dict) else v for k, v in d.items() if not k.startswith("_")}
        print(json.dumps(clean(cfg), indent=1, ensure_ascii=False))
        return
    problems = []
    for key in ("blocked_companies", "location_flags"):
        value = cfg.get(key, [])
        if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
            problems.append(f"{key} must be a list of non-empty strings")
    if cfg.get("linkedin", {}).get("mode") not in ("email", "read"):
        problems.append("linkedin.mode must be email or read")
    days = cfg.get("same_company_days")
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        problems.append("same_company_days must be a whole number of days")
    prof = ROOT / "profile" / "profile.md"
    text = prof.read_text(encoding="utf-8") if prof.exists() else ""
    if not text:
        problems.append("profile/profile.md is missing (run /seekter-init)")
    for key, what in REQUIRED_PROFILE.items():
        if text and "{{" + key + "}}" in text:
            problems.append(f"profile/profile.md has no {what} yet ({{{{{key}}}}}; run /seekter-init or fill it in)")
    docs = ROOT / "profile" / "documents"
    if text and not any(p.suffix.lower() in (".pdf", ".docx", ".doc") for p in docs.glob("*")):
        problems.append("profile/documents/ has no CV file (.pdf, .doc or .docx)")
    try:
        mine = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mine = {}
    left = _defaulted(_defaults(), mine)
    if left:
        print("using defaults for: " + ", ".join(left))
    if problems:
        print("\n".join("problem: " + p for p in problems))
        sys.exit(1)
    print("settings ok")


def rows(since=None, status=None):
    out = []
    for r in all_apps():
        d = r.get("applied") or r.get("updated") or ""
        if since and d < since:
            continue
        if status and r.get("status") != status:
            continue
        out.append(r)
    out.sort(key=lambda r: (r.get("applied") or r.get("updated") or ""), reverse=True)
    return out


def cmd_list(a):
    for r in rows(a.since, a.status):
        print(f"{r.get('applied') or r.get('updated')} | {r.get('status'):<12} | {r.get('company')} | {r.get('role')} | {r.get('url','')}")


def counts(rs):
    c = {s: 0 for s in STATUSES}
    for r in rs:
        c[r.get("status") or "pending"] = c.get(r.get("status") or "pending", 0) + 1
    return c, sum(c[s] for s in SENT)


def cmd_stats(a):
    c, sent = counts(rows(a.since))
    print(json.dumps({"total": sum(c.values()), "sent": sent, **c,
                      "response_rate": round((c["interviewing"] + c["offer"] + c["rejected"]) / sent, 3) if sent else None},
                     indent=1))


def cmd_normalize(a):
    files = rows_changed = 0
    for d in month_dirs():
        recs = read_skips(d / "skipped.md")
        # every record, not `any(...)`: that short-circuits on the first change
        # and leaves the rest of the table unnormalised.
        hits = sum(1 for r in recs if fix_meta(r))
        if hits:
            rows_changed += hits
            if not a.dry_run:
                write_skips(d / "skipped.md", recs)
        for f in sorted(d.glob("*.md")):
            if f.name in ("skipped.md", "README.md"):
                continue
            r = read(f)
            path, body = r.pop("_path"), r.pop("_body")
            r.pop("_kind")
            clean = dedupe_log(body)
            fixed = bool(fix_meta(r))  # always run: it rewrites the metadata in place
            if fixed or clean != body:
                files += 1
                if not a.dry_run:
                    write(path, r, clean)
    verb = "would change" if a.dry_run else "normalised"
    print(f"{files} files and {rows_changed} skip rows {verb}")


def _link(r, base: Path):
    if r["_kind"] == "row":
        return _md_text(r.get("role", ""))
    role = _md_text(r.get("role", ""))
    return f"[{role}]({r['_path'].relative_to(base).as_posix()})"


def _table(rs, base, cols=("Date", "Company", "Role", "Status", "Fit", "Posting")):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rs:
        vals = {"Date": r.get("applied") or r.get("updated", ""), "Company": _md_text(r.get("company")),
                "Role": _link(r, base), "Status": _cell(r.get("status", "")), "Fit": _cell(r.get("location_fit", "")),
                "Posting": f"[link]({_md_url(r['url'])})" if _md_url(r.get("url", "")) else "",
                "Next step": _md_text(section(r.get("_body", ""), "Notes")[:160])}
        out.append("| " + " | ".join(vals[c] for c in cols) + " |")
    return out


def cmd_index(a, quiet=False):
    rs = rows()
    since30 = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    lines = ["# Applications", "", f"Generated by `scripts/seekter.py` on {TODAY}. Don't edit by hand.", ""]
    pend = [r for r in rs if r.get("status") == "pending"]
    live = [r for r in rs if r.get("status") in ("interviewing", "offer")]
    recent = [r for r in rs if r.get("status") in ("applied", "rejected", "closed") and (r.get("applied") or r.get("updated", "")) >= since30]
    lines += [f"## Needs you ({len(pend)})", "", "Forms waiting for a CAPTCHA, an account, a decision or an answer only you have.", ""]
    lines += _table(pend, APPS, ("Date", "Company", "Role", "Next step")) if pend else ["Nothing."]
    lines += ["", f"## In progress ({len(live)})", ""]
    lines += _table(live, APPS) if live else ["Nothing yet."]
    lines += ["", f"## Sent in the last 30 days ({len(recent)})", ""]
    lines += _table(recent, APPS) if recent else ["Nothing."]
    lines += ["", "## By month", "", "| Month | Sent | Replies | Interviewing / offer | Pending | Skipped |", "|---|---|---|---|---|---|"]
    for d in reversed(month_dirs()):
        mr = [r for r in rs if r["_path"].parent == d]
        c, sent = counts(mr)
        lines.append(f"| [{d.name}]({d.name}/README.md) | {sent} | {c['rejected'] + c['interviewing'] + c['offer']} | "
                     f"{c['interviewing'] + c['offer']} | {c['pending']} | [{c['skipped']}]({d.name}/skipped.md) |")
        # per-month page
        ml = [f"# {d.name}", "", f"Generated by `scripts/seekter.py` on {TODAY}.", "",
              " · ".join(f"**{s}** {c[s]}" for s in STATUSES if c[s]), ""]
        files = [r for r in mr if r["_kind"] == "file"]
        ml += _table(files, d) if files else ["No applications this month."]
        ml += ["", f"Skipped postings ({c['skipped']}): [skipped.md](skipped.md)", ""]
        _atomic_write(d / "README.md", "\n".join(ml))
    c, sent = counts(rs)
    lines += ["", f"All time: **{sent} sent** · " + " · ".join(f"{s} {c[s]}" for s in STATUSES if c[s]), ""]
    APPS.mkdir(exist_ok=True)
    _atomic_write(APPS / "README.md", "\n".join(lines))
    if not quiet:
        print(f"applications/README.md ({len(rs)} records, {len(month_dirs())} months)")


def cmd_migrate(a):
    """Move a v1 tracker (applications/<status>/*.md) to the month layout."""
    old = [APPS / s for s in STATUSES if (APPS / s).is_dir()]
    if not old:
        sys.exit("nothing to migrate: no applications/<status>/ folders")
    n_files = n_rows = 0
    for d in old:
        for f in sorted(d.glob("*.md")):
            r = read(f)
            body = r.pop("_body"); r.pop("_path"); r.pop("_kind")
            r["status"] = r.get("status") or d.name
            if r["status"] == "skipped":
                r["notes"] = section(body, "Notes") or section(body, "Why it fits")
                save(r)
                n_rows += 1
            else:
                date = r.get("applied") or r.get("updated") or f.name[:10]
                path = APPS / month_of(date) / f.name
                fix_meta(r)
                write(path, r, body)
                n_files += 1
            if not a.keep_old:
                f.unlink()
        if not a.keep_old:
            try:
                d.rmdir()
            except OSError:
                pass
    cmd_index(None)
    print(f"migrated {n_files} files and {n_rows} skip rows" + (" (old folders kept)" if a.keep_old else ""))


def main():
    ap = argparse.ArgumentParser(description="Seekter tracker")
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("check"); c.add_argument("url"); c.add_argument("--company")
    c.set_defaults(fn=cmd_check)
    cm = sp.add_parser("check-many"); cm.set_defaults(fn=cmd_check_many)
    ft = sp.add_parser("filter-test")
    for option in ("titles", "date", "keep", "drop"):
        ft.add_argument("--" + option)
    ft.set_defaults(fn=cmd_filter_test)
    qu = sp.add_parser("queue"); qu.add_argument("--date"); qu.set_defaults(fn=cmd_queue)
    ad = sp.add_parser("add")
    for f in ("company", "role", "url"):
        ad.add_argument("--" + f, required=f != "url", default="")
    ad.add_argument("--status", default="applied")
    for f in ("source", "ats", "apply-type", "location-fit", "remote-scope", "fit", "posted", "applied",
              "date", "why", "notes", "answers", "log"):
        ad.add_argument("--" + f, default="")
    ad.add_argument("--force", action="store_true")
    ad.add_argument("--no-index", action="store_true", help="skip regenerating the README index (bulk adds)")
    ad.set_defaults(fn=cmd_add)
    mv = sp.add_parser("move"); mv.add_argument("target"); mv.add_argument("status"); mv.add_argument("--note", default="")
    mv.add_argument("--answers", default="", help="free-text answers as submitted, appended to the record")
    mv.set_defaults(fn=cmd_move)
    ls = sp.add_parser("list"); ls.add_argument("--status"); ls.add_argument("--since"); ls.set_defaults(fn=cmd_list)
    ix = sp.add_parser("index"); ix.set_defaults(fn=cmd_index)
    nm = sp.add_parser("normalize", help="lowercase enums, derive ats from url, fix ats-in-source, drop repeated log lines")
    nm.add_argument("--dry-run", action="store_true"); nm.set_defaults(fn=cmd_normalize)
    se = sp.add_parser("settings", help="show the settings in effect; --check validates them and the profile's required values")
    se.add_argument("--check", action="store_true"); se.set_defaults(fn=cmd_settings)
    st = sp.add_parser("stats"); st.add_argument("--since"); st.set_defaults(fn=cmd_stats)
    mg = sp.add_parser("migrate", help="convert applications/<status>/ folders to applications/<YYYY-MM>/")
    mg.add_argument("--keep-old", action="store_true"); mg.set_defaults(fn=cmd_migrate)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
