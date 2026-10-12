# Changelog

The version is the git tag; there is no version file. Each release is also on
[the releases page](https://github.com/selfishprimate/seekter/releases) with the
same text.

## Unreleased

`seekter.py queue` turns the API sweeps into a deduped list for the apply half
of a run, with title filters and same-company hold hints. Candidates added by
hand survive the next rebuild; every posting still gets opened and checked
before a form.

The employer sweep now reads SmartRecruiters and Recruitee, and keeps posting
dates from all six boards. Failed requests are reported, so an outage cannot
quietly look like an empty market.

`move` refuses paths outside the tracker. API text is cleaned before it reaches
the terminal, table cells are escaped, and atomic writes keep file permissions.

Setup now calibrates title filters against real postings before saving them;
`filter-test` shows what falls on each side later. Sweeps record every dropped
posting and its reason. Blocked companies stay out, while location phrases mark
and demote postings for a closer read, keeping the decision open and auditable.

## v0.6.0 — 10 October 2026

Doğan Can Karataş's second set of runs in Turkey (#45): a new application
system, two forms that end in a hand-off, and Kariyer.net from a logged-in
account. Plus one thing Lever does on its own.

### New: Peoplise

A Turkish recruiting platform used by banks and large employers
(`live.peoplise.com/<co>/Application/...`). The landing page is already the
first step of the form, and all three legal checkboxes on it are enforced
although none is marked required. On one bank tenant the third one shares the
candidate's data with the parent holding company. That is not the
application's own consent, so Seekter fills the rest and hands the posting
back to you.

### Mantu careers and BambooHR end in a hand-off

- **Mantu careers** (`careers.mantu.com`, Amaris and the other Mantu brands)
  has a required "I agree to Mantu's Terms and Conditions" box. That is a terms
  acceptance, so it is yours. The posting header names the job's working
  language, which settles a question the English description can leave open.
- **BambooHR** tenants can require a street address and postcode, and a
  honeypot field sits first in the form; it is never filled. The visible
  reCAPTCHA makes every BambooHR form a hand-off anyway.

### Kariyer.net from a logged-in account

Measured on 9 and 10 Oct:

- Logged in, `fetch()` returns an empty page shell for postings and searches.
  Navigate to each page instead.
- The search is not a title search: a work-model word in the keywords empties
  the list, and `node.js` finds 2 cards where `node` finds 16. Filter the cards
  on your own `title_keep`.
- Some postings are open only to candidates with a disability and say so only
  when you submit. Nothing is sent; it is recorded as a skip.
- Some employers add a data-consent box under their questions. It is ticked
  only when your profile gives consent for application data; otherwise the
  posting is handed back. Postings under one group account count as one company
  for the same-company window.
- With the Chrome window in the background, coordinate clicks land nowhere.
  The notes say how to submit and confirm anyway.

### Lever already knows you applied

A posting you applied to by hand, before Seekter tracked anything, lands on
Lever's `/already-received` page after submit, with the date of your earlier
application, and nothing new is sent. Measured 9 Oct on an application from
six months earlier. Seekter now records it as applied on that date instead of
treating it as an error.

### Upgrading

Nothing to migrate. Your `profile/`, `applications/` and `runs/` are
git-ignored and untouched.

## v0.5.0 — 9 October 2026

Two sources that need no browser: the job boards of the companies you would
most like to work for, read straight from their applicant tracking system, and
the monthly Hacker News "Who is hiring?" thread.

### New: an employer watchlist

Most product companies publish their job board as open JSON through Greenhouse,
Ashby, Lever or Workable. List the ones worth watching under `employers` in
`profile/settings.json` (name, ats, slug) and `scripts/employer_sweep.py` reads
every board in step 1, one a second, filters titles with your `title_keep` and
`title_drop`, drops what the tracker already holds and prints the location line
the employer wrote. A posting is seen the day it opens, before any aggregator
copies it.

Measured 9 Oct: 29 boards, 1,761 postings, 34 title matches, 2 applications.
Most matches died on a location line the API had already returned, so read that
column first. The slug is the board's own id, not the company's name: one
obvious slug belonged to a defence contractor and another company's was its
name plus two digits. Open the board once before adding a row.

### New: Hacker News "Who is hiring?"

`python3 scripts/employer_sweep.py --hn` reads the month's thread through the
public Algolia API and keeps the posts that mention your titles. The October
thread had 248 posts, 29 mentioned the titles, about 5 were real roles in the
discipline and 1 became an application. Many posts are by founders and some
apply by email, which stays yours to send.

### Every Hacker News post looked like the same posting

HN posts all live at `/item?id=<n>`, and the tracker ignored the query, so a
whole thread keyed as one posting: a post skipped in the morning made the next
application from the same thread report DUPLICATE. Posts now key as `hn:<id>`.
This only affected anyone who logged HN links by hand before this release.

### Upgrading

Nothing to migrate. To use the watchlist, add `employers` rows to
`profile/settings.json` (the template shows the shape). If you logged a Hacker
News link before this release, its row in `skipped.md` keeps the old key
`news.ycombinator.com/item`; change it to `hn:<id>`. Your `profile/`,
`applications/` and `runs/` are git-ignored and untouched.

## v0.4.0 — 8 October 2026

The first release with a contributor's runs in it: Doğan Can Karataş measured
the kit for a candidate who applies only in their own country, on Windows
(#39). It brings a new board, two new form systems and a fix that was hiding
every job on one platform.

### On Windows the freehire sweep found nothing

`freehire_sweep.py` decoded curl's output with the console's legacy code
page, so every response failed to parse and the sweep reported 0 jobs, which
reads exactly like a quiet market. The summary line then crashed on its arrow.
Both are fixed. If you ran Seekter on Windows before this release, step 1 never
worked for you.

The same script refused a profile with `home_country` set and `regions` empty.
That is the only correct setup when freehire's own region for your country
returns nothing (`regions=turkey` returned 0 on 6 Oct while `countries=TR`
returned 314), so a home-country-only candidate could not sweep at all. Now
either one is enough, and a test holds it.

### LinkedIn already knew what you applied to by hand

In `read` mode the job detail carries `applyingInfo`, which says when you
applied through LinkedIn yourself. The run never read it, so postings you had
already sent through Easy Apply came back on your list to send. Measured 8 Oct:
four of them. The run now records those as applied and leaves them off.

A thin geo with a short window returns padding rather than keyword matches;
on a small market, search the country over a week.

### New: Kariyer.net, Heroty and Manatal

- **Kariyer.net**, Turkey's largest job board: the search URL shape that
  actually filters by keyword, and the logged-in apply flow, which pre-fills
  answers from earlier applications. Read each one against your profile before
  sending.
- **Heroty** keeps closed postings readable until you press apply.
- **Manatal** (`careers-page.com`) does nothing on submit until a terms box is
  ticked. Accepting terms is yours, so it becomes a hand-off.
- freehire's Turkish postings are mostly leads behind a sign-up wall, not apply
  links; resolve the employer first.

### Ashby sends nothing while the CV is still uploading

The first submit after a CV upload returned "We're updating your application
(e.g. uploading files), please try again" on all four Ashby forms filled on 7
and 8 Oct, and sent nothing. Waiting a few seconds and submitting again gives
the real result.

### Upgrading

Nothing to migrate. To use Kariyer.net, add it to `boards` in
`profile/settings.json`. Your `profile/`, `applications/` and `runs/` are
git-ignored and untouched.

## v0.3.1 — 7 October 2026

Reading is paced like a person, and a verification page is a stop sign.

### LinkedIn reads kept a fixed rhythm

In `read` mode every LinkedIn request was followed by exactly 3 seconds of
waiting. Detection reads volume and rhythm, and a gap that never varies is a
rhythm no person keeps. `linkedin.read_limits` gains `max_gap_seconds`, and
each pause is now drawn at random between `min_gap_seconds` and it: 4 to 9
seconds by default. A run reads a little slower; the limits on searches and
details are unchanged.

### Boards read in the browser had no pace at all

Only LinkedIn had written limits. Indeed, Glassdoor and any listing page
Seekter scrolls in your Chrome had no rule for the gap between pages, and no
rule for what a Cloudflare or CAPTCHA page meant. They now get the same random
4 to 9 seconds, one page at a time, and a verification page ends that board for
the run: it goes in the report and the run moves on. The one exception is a
cause the board's own file has measured, like Indeed's empty location, which is
fixed and retried once. Public APIs stay sequential.

### Upgrading

Nothing to migrate. If your `profile/settings.json` sets `min_gap_seconds: 3`,
it keeps 3 as the lower bound and 9 from the template as the upper; raise it to
4 for the new default. Your `profile/`, `applications/` and `runs/` are
git-ignored and untouched.

## v0.3.0 — 6 October 2026

Settings move into one file with defaults, and a setup you stop halfway still
runs.

### One `{{` in the profile stopped every run

`/seekter-run` refused to start while `profile/profile.md` still held a single
`{{…}}`, even when the missing answer was optional, like a notice period or a
second CV. A setup interrupted at question 20 of 40 gave you nothing to run.
Now only four things stop a run, because no default can stand in for them: the
profile itself, the application email, the default CV and the country you live
and work in. Every other gap is asked when a form needs it, the way `ASK` always
was.

### Every switch in one file

Settings were split between `profile/search.json` and sentences inside
`profile/profile.md`, so changing one meant knowing where it lived.
`profile/settings.json` now holds every switch and number: queries, title
filters, boards, the LinkedIn mode and its limits, the same-company window.
Each key is documented in `templates/settings.json`, and a key you leave out
runs on the template's default. You can edit the file by hand.

- `/seekter-init` creates it right after the disclaimer and writes each answer
  the moment you give it.
- `python3 scripts/seekter.py settings` shows what is in effect, and
  `settings --check` lists what is still on a default and says plainly what, if
  anything, needs fixing.
- Guardrails are not settings. Nothing in the file can make Seekter solve a
  CAPTCHA, create an account, accept terms for you, or fill LinkedIn Easy Apply.

### Upgrading

Nothing to do by hand. Your `profile/search.json` is renamed to
`profile/settings.json` the first time a command reads it, with every value
unchanged. If you have created a `settings.json` yourself and still have the old
file, Seekter warns instead of guessing; move what you need and delete
`search.json`. Your `profile/`, `applications/` and `runs/` are otherwise
untouched.

## v0.2.2 — 6 October 2026

A bug-fix release. The 30-day rule from 0.2.1 could hold a company you had never
applied to, and a held posting is skipped, so the cost was an application.

### The same-company check matched part of a word

`check` and `check-many` compared company names as substrings. A short name
matched any company that contained it: measured 5 and 6 Oct, "telli" matched
Intellias and Intermedia Intelligent, and "Flex" matched WorkFlex and Engiflex.
Both were held under the 30-day rule, and the run reported them as on hold
rather than new, which reads like a correct decision. If you are on 0.2.1, any
posting held by a company whose name is part of another word may have been
skipped for nothing; the report's "On hold" list names the earlier role, which
is the place to check.

The check now matches the name or whole words of it. "Hays" still matches
"Hays Poland"; "Flex" no longer matches "WorkFlex".

### A record's answers could only be added by hand

A hand-off often gets its free-text answers after the record exists. There was
no command for it, and the tracker is meant to be written only through the CLI.
`move` now takes `--answers` and appends them to the record's Answers section.

### Running the same move twice wrote the log line twice

A batch of rejections run twice doubled the line on 27 records. `move` now skips
a line identical to the last one, and `normalize` collapses adjacent repeats in
files you already have.

### Also

- `MANIFESTO.md` is new: why Seekter exists and the principles it is built on.
- `CONTRIBUTING.md` asks for a proposal issue before anything larger than a fix.
- The README no longer lists salary among the rules postings are filtered
  against; pay is only ever an answer to a form question.
- Ashby: the application form can be read before a tab is opened, from the job
  page's own public endpoint.
- Teamtailor: a knockout answer dims the form and looks exactly like a frozen
  page. The tell is a line under the radios.
- Working Nomads: apply links resolve with a plain `curl`, no browser needed.

### Upgrading

Nothing to migrate. Replace the files and keep your `profile/`, `applications/`
and `runs/`; they are git-ignored and not touched. To clear doubled log lines
from an earlier double run, run `python3 scripts/seekter.py normalize`.

## v0.2.1 — 4 October 2026

A bug-fix release. Both fixes are about the tracker letting something through
that it should have stopped, and neither showed up as an error.

### A second application to the same company could be rejected silently

Greenhouse lets an employer auto-reject a candidate's further applications to a
department within a window, or after a rejection. The rejected application is
marked "blocked by auto reject rule", and the candidate is told only if the
employer has switched the email on. Seekter treated a second role at the same
company as a fresh posting, and on a good day it could send several. Measured
2 Oct: two roles at one company and a second role at another went out the same
afternoon. If the employer uses that rule, the second one may never have been
read, and to a recruiter it looks like the mass applying they filter out.

`check` now holds the company: it exits 2 with `HOLD` when there is already an
application, a pending hand-off or a rejection there within the window, or an
interview or offer at any date. The run skips a held posting, says which earlier
role and date held it, and lists it under its own heading in the report so you
can overrule it. When one run finds two roles at the same company, only the
better fit goes out. A link you bring yourself counts as your decision and is
applied, with a note. The window is `same_company_days` in `profile/search.json`,
30 days by default.

### `check` passed a bare LinkedIn id as new

`check-many` has always read a bare number as a LinkedIn job id. `check` did not:
it keyed the number as it was, matched nothing, and printed NEW for a job
already in the tracker. Since 0.2.0 tells the run to dedup alert-mail postings
on their LinkedIn id, that check could have let a duplicate through. Both
commands now read the number the same way.

### Also

- One local-language search term in the Indeed notes was spelled in plain
  ASCII, so the 0.2.0 language sweep missed it.

### Upgrading

Nothing to migrate. Without `same_company_days` in `profile/search.json`, the
window is 30 days; add the key to change it. `profile/`, `applications/` and
`runs/` are git-ignored and untouched.

## v0.2.0 — 4 October 2026

The release that changes what Seekter does on LinkedIn. Until now it filled
Easy Apply forms and searched LinkedIn through its internal API, and LinkedIn
restricts accounts for exactly that. Nothing in the kit said so. This version
stops acting on LinkedIn, makes reading it a choice you make knowingly, and
tells you what you take on before the first run.

### Seekter was automating LinkedIn, and LinkedIn restricts accounts for it

LinkedIn's help page on prohibited software does not permit browser extensions
that "scrape, modify the appearance of, or automate activity on LinkedIn's
website", and says accounts that use them risk being restricted or shut down.
Seekter drives your browser through an extension. On 0.1.x a daily run filled
Easy Apply, ran around 40 searches through LinkedIn's internal API and harvested
the notification feed, all from your own account. If you have been running it
daily, that is what LinkedIn has been seeing. The User Agreement also rules out
the obvious fallback of a second account.

Now:

- **Easy Apply is never filled, in any mode**, and no action is taken on
  LinkedIn: no saving, following, messaging or alert editing. Easy Apply
  postings come back to you in the report as a list to send yourself.
- **By default Seekter does not open LinkedIn at all** (`linkedin.mode: email`).
  It reads your LinkedIn job-alert emails in your inbox and finds each posting
  on the employer's own application system. A digest mail shows only about six
  of its matches (measured 4 Oct: "30+ new jobs" above six cards), so narrow
  alerts lose much less than broad ones.
- **Reading LinkedIn is opt-in** (`linkedin.mode: read`), and setup asks with a
  plain warning that it is against LinkedIn's terms. It is read-only and
  limited: at most 15 searches and 60 job details per run, 3 seconds apart, one
  at a time. A "too many requests" error, a security-check or login redirect, or
  an unusual-activity page stops it for the run and switches the mode back to
  `email`.

The cost is volume. On 2 Oct the searches found 352 postings and the alert
feed 63, so `email` mode on its own finds far fewer.

### Your own links come first

Finding a posting takes a person seconds; filling its form is the work. Paste
links into the chat or drop them in `profile/links.txt` (git-ignored), and they
are filled before every other source. A LinkedIn job link is fine: Seekter
finds the employer's own form behind it, or hands it back to you if it is Easy
Apply.

### Nobody was told what they were taking on

Seekter sends applications in your name, uses other sites under their terms
and runs on a paid Claude plan. None of that was written down, and "Seekter is
free" read as the whole story.

- `DISCLAIMER.md`: what you are responsible for, whose terms you follow, no
  warranty, no affiliation, and that Seekter is free but running it is not.
- `PRIVACY.md`: Seekter collects nothing, and this is where your data goes
  while it works: Anthropic, the employers you apply to, the job sources, and
  your inbox, read-only.
- `/seekter-init` now opens with one sentence covering those points and goes on
  only on a yes. The README has the same three points above Quick start.

### The references spoke one candidate's language

The kit was written during one person's job search, and twenty-odd reference
files used that person's language and country as their examples: translated
button labels, local search terms, a city spelled two ways, a dial code. A user
elsewhere would have needed their own copy of each line. Every lesson is still
there, now written to hold in any language and country, with `<CITY>`,
`<COUNTRY>` and `+<code>` where a place is needed.

### Also

Twelve form-system traps measured on 2 Oct, among them:

- Ashby can show a filled field as "Missing entry"; filling it a second way
  clears it, and the first Submit may only blur the field.
- Lever validates every `urls[...]` field as a URL, so free text in one fails
  the submit silently.
- Teamtailor's required Address can be a location search box that a script
  cannot fill.
- CleverStaff uploads the CV and then rejects the form anyway, until the file is
  tied to its field.
- Oracle Cloud HCM creates an account on its first screen, so it is handed to
  you.
- Google Forms records the signed-in Google account's email with an upload,
  which may not be the address your profile names.

### Upgrading

- The next `/seekter-run` asks you the disclaimer once. A no stops the run.
- Your profile starts in `email` mode. Create your LinkedIn job alerts with
  **Email** delivery, one per row of `linkedin.searches` in
  `profile/search.json`, narrow rather than broad. To keep reading LinkedIn
  searches yourself, set `linkedin.mode` to `read`; Easy Apply stays off either
  way.
- Nothing else to migrate. `profile/`, `applications/` and `runs/` are
  git-ignored and untouched.

## v0.1.2 — 2 October 2026

A bug-fix release. Two of the three fixes are about things the kit did without
telling you: one sent an application a step early, the other left rejections
looking like open applications.

### On a one-page Easy Apply form, "next" was Submit

The Easy Apply driver advances by taking the last non-cancel button in the
modal. On a multi-page form that is Next or Review. On a form with a single page
it is **Submit**, and the driver had no way to tell the two apart.

Measured 2 Oct: the modal opened with no page counter, the first advance sent
the application, and there was no review step. Nothing false went out, because
the contact page is checked before advancing. But the review step is also where
the pre-ticked "follow this company" box gets unticked, and that opt-in was left
on. If you use Easy Apply on 0.1.1, some of your one-page applications have
probably gone out the same way.

The driver now reads the page counter before every advance. A modal with no
counter is one page, so the next press submits, and the run reads the whole
form before deciding to.

### The blacklist never saw the company name

The aggregator list and your profile's blacklist are both lists of company
names. The filter ran them over the posting's title, description and apply
host, which do not carry the company. Measured 2 Oct: an aggregator named in the
skill's own list got through every filter and was only caught once its job page
was open. The company is now part of what the filter reads. On LinkedIn the
reliable source for it is the first `"name"` in the detail response, because
`companyDetails` often comes back as `?`.

### Some rejections did not read as rejections

`/seekter-log` finds rejections with a regex. It had no phrasing for a role
being filled or closed, and it had `not moving forward` but not `unable to move
forward`. Measured 2 Oct: one rejection used both phrasings and matched nothing.
A miss does not look like an error. It looks like an application that is still
open, so the row stays `applied` indefinitely. The list now covers the
filled/closed phrasings and `unable to move` / `unable to proceed`.

The same sweep found Outlook's `[role=option]` rows working again, four days
after they had disappeared. The inbox reference now says to probe all three list
selectors and use whichever one answers.

### Also

- `CHANGELOG.md` is new since 0.1.1. The 0.1.1 zip did not include it, so this
  is the first download that does. `/seekter-git` now covers releases too: once a
  pull request is merged, it can write the entry and publish the version.
- The README opens on a new cover image. The image it used to point at had been
  deleted, so the first line had been broken.
- Indeed: the tenth measured pass returned nothing new for the tenth time.
- Jobicy: eight tags returned zero rows from an API that was otherwise
  answering normally.
- LinkedIn: notifications against searches, measured a seventh time. 13 ids of
  63 overlapped.

### Upgrading

Nothing to migrate. Replace the files and keep your `profile/`, `applications/`
and `runs/`; they are git-ignored and are not touched.

## v0.1.1 — 1 October 2026

A bug-fix release. If you are on 0.1.0 and run `/seekter-run`, one of the five
sources has been quietly doing nothing for you, and that is what this fixes.

### LinkedIn Easy Apply never ran if your LinkedIn is not in English

The Easy Apply driver matched on the English button names: "Easy Apply", "Next",
"Review", "Submit application". On an account set to another
interface language those labels are translated, and every check failed.

It fails silently, which is what makes it expensive. There is no error. The
detection just reports that a posting has no Easy Apply button, which reads
exactly like a posting that has closed. Measured on a 250-posting sweep: the
entire Easy Apply tier was skipped, sixty candidates went unopened, and two live
postings were recorded as closed. The run's own priority order calls that tier
the last one to cut, because it costs about six calls and no upload.

The driver no longer matches button text at all. It finds the modal from its
heading and takes the last non-cancel button inside it, which works in any
language.

Four more Easy Apply mechanics were measured and written down at the same time:
the modal opens on a JS MouseEvent dispatch and on nothing else; the
work-authorization questions arrive in either order, so they have to be read
rather than answered by position; the coordinate frame can change between two
postings inside one run; and the email field is a dropdown that may hold more
than one address.

### The queue the skill promised now actually exists

`/seekter-run` told you that if a session ran short, "the queue survives into the
next run". Nothing ever wrote it down. A run that sweeps 250 postings and applies
to eight has resolved 240 apply URLs that live only in a browser tab, and the
next session starts from nothing.

The run now writes `runs/<date>/queue-A.md` before its report, one row per
unworked posting with its resolved apply URL, and says in the file that the A/B/C
letter is a sorting hint rather than a verdict.

### Also

- Ashby: a form's own country-of-residence list outranks the location in the
  posting header. A header naming four countries looks like a closed door; the
  form offering yours is the employer saying otherwise.
- Indeed: ninth measured pass, ninth with nothing new.
- LinkedIn: notifications against searches, measured a sixth time. 7 ids of 44
  overlapped. The two channels still do not substitute for each other.

### Upgrading

Nothing to migrate. Replace the files, keep your `profile/`, `applications/` and
`runs/`, which are git-ignored and untouched.

## v0.1.0 — 30 September 2026

First tagged version. Seekter had been in daily use for a month; this is the
point where that stopped being an untagged moving target.

- **12 documented job sources**, plus a file on the ones that were measured and
  found not worth the calls.
- **24 documented application form systems**, each written from real filled
  forms, plus a file of one-offs.
- A markdown tracker written only through `scripts/seekter.py`, with a normalised
  `job_key` so the same posting reached through LinkedIn, an aggregator and the
  company's own site is still caught as one.
- **39 tests** over the tracker CLI: `python3 -m unittest discover tests`.
- Guardrails that do not bend: no CAPTCHAs, no account creation, no passwords, no
  accepting terms of use, no messages sent as you, and no answer it cannot verify
  from your profile.

Numbered 0.1.0 rather than 1.0.0 because the structure is about to move: a Claude
Code plugin is next, which means the skills relocate and the tracker root stops
being "wherever you cloned this". 1.0.0 is the version where that has settled.
