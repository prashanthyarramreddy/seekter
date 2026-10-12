# Direct employer sources


Blocked companies are excluded, location-flagged rows carry `flag` and are demoted rather than deleted, and dropped postings with their reasons are recorded in `runs/<date>/employers-dropped.json`.
## The watchlist: `python3 scripts/employer_sweep.py`

Most product companies publish their job board as open JSON through their applicant tracking system. Reading it directly sees a posting the day it opens, with the employer's own location line, before any aggregator copies it and before LinkedIn shows it to everyone. No login, no browser, no terms to click.

The companies come from `employers` in `profile/settings.json`, one row per company:

```json
{"name": "Example", "ats": "ashby", "slug": "example"}
```

| `ats` | Board the slug comes from | API the script reads |
|---|---|---|
| `greenhouse` | `job-boards.greenhouse.io/<slug>` | `boards-api.greenhouse.io/v1/boards/<slug>/jobs` |
| `ashby` | `jobs.ashbyhq.com/<slug>` | `api.ashbyhq.com/posting-api/job-board/<slug>` |
| `lever` | `jobs.lever.co/<slug>` (or `jobs.eu.lever.co`) | `api.lever.co/v0/postings/<slug>?mode=json`, then the EU host |
| `workable` | `apply.workable.com/<slug>` | `apply.workable.com/api/v1/widget/accounts/<slug>` |
| `smartrecruiters` | `jobs.smartrecruiters.com/<slug>` | `api.smartrecruiters.com/v1/companies/<slug>/postings?limit=100` |
| `recruitee` | `<slug>.recruitee.com` | `<slug>.recruitee.com/api/offers/` |

The script waits a second between boards, filters titles with the profile's `title_keep` / `title_drop`, drops anything already in the tracker, writes `runs/<date>/employers.json` and prints one line per candidate. A slug that returns nothing is printed as a `problem:` line rather than guessed at.

**The slug is the board's own id, not the company's name, and the name can belong to someone else.** Measured 9 Oct, building a 30-company list: one product company's obvious slug on Ashby was a defence contractor's board, seven on-site and hybrid roles at US government sites, and another company's Workable account turned out to be `<name>91`. Open the board URL once before adding a row and check that the jobs are the company you meant.

**What a watchlist is worth, measured 9 Oct:** 29 boards, 1,761 postings, 34 title matches after the profile's filters, 2 applications sent. Most matches died on a location line the API had already returned ("North America (Remote)", "Remote, United States"), so read the `location` column before opening anything.

**Pick employers for where they hire, not for how well known they are.** A company that hires remotely only in the US adds rows to read and nothing to apply to. The list earns its place with companies that name the candidate's region or a time-zone window in their postings, or that sponsor where the candidate would move.

**The location line is the employer's own, so it binds, but it is still a label.** Apply the country-list and time-zone rules in `_core.md` and the run skill: an Ashby "The Americas / Europe" header on 9 Oct came with a form question about working hours, not residence, so a candidate living in neither region but able to work those hours could answer it truthfully.

## Checking one company by hand

- **Greenhouse public board API** (any tenant, no auth): `https://boards-api.greenhouse.io/v1/boards/<tenant>/jobs`. Add `?questions=true` to a single job (`/jobs/<id>?questions=true`) to read the form's questions before opening it. Filter the list as below; the job URL is `job-boards.greenhouse.io/<tenant>/jobs/<id>`.
```js
JSON.parse(document.body.innerText).jobs.filter(x=>/design/i.test(x.title))
 .map(x=>x.id+' | '+x.title+' | '+x.location.name)
```
- **Ashby company boards** (`jobs.ashbyhq.com/<co>`) list the open countries for every role; read them before applying. The posting API returns them as `location` plus `secondaryLocations`.
- **Workable** also serves one job (`/api/v2/accounts/<acct>/jobs/<code>`) and its form (`/api/v1/jobs/<code>/form`) as JSON.
- **When you find a strong company, check its whole careers page.** LinkedIn doesn't show every posting. If it is worth checking twice, it is worth a row in `employers`.
- `jobs.siemens.com` ("Careers Marketplace") is a single-employer portal covering Siemens AG + Healthineers. Monthly check. The session drops silently mid-flow, so verify the session after each step.
- Workday: after applying, check Candidate Home "Suggested Jobs"; `/apply/useMyLastApplication` makes repeat applications cheap.
