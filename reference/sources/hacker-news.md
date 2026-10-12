# Hacker News "Who is hiring?"


Blocked companies are excluded, location-flagged rows carry `flag` and are demoted rather than deleted, and dropped postings with their reasons are recorded in `runs/<date>/hn-dropped.json`.
`python3 scripts/employer_sweep.py --hn`

On the first weekday of every month the `whoishiring` account opens an "Ask HN: Who is hiring?" thread, and companies post one top-level comment each. The posts come from the people doing the hiring, often founders, and many link straight to the role or to an email address. It is read through the public Algolia API, one search and one item fetch, with no login:

- the thread: `hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring`, then the first hit titled "Ask HN: Who is hiring?";
- its comments: `hn.algolia.com/api/v1/items/<id>`.

The script keeps the posts whose text matches `title_keep`, writes `runs/<date>/hn.json` (id, the first 200 characters, a remote flag, the first three links, the comment URL) and prints one line per post.

**The header line is a convention, not a schema.** Most posts open with `Company | Role | Location | REMOTE/ONSITE | Full-time`, and that line is what to read first. Some put the role in the body, list several roles at once, or start with prose. Read the header, then the post, then the linked careers page for the location rule; the post's own "REMOTE" often means "REMOTE (US)" two words later.

**A title match is a mention, not a role.** `title_keep` runs over the whole post, so a company hiring engineers that says it works closely with "our designer" matches too. Measured 9 Oct on the October 2026 thread: 248 posts, 29 matched, about 5 were roles in the candidate's discipline, and 1 became an application. Expect to open a handful of posts a month, not dozens.

**Nothing here is deduped against the tracker.** A post's only id is the comment, and the application goes through the company's own page, so the two never share a key: on 9 Oct a post applied to that morning was still in the list that afternoon. Run `check` on the apply URL you resolve from the post, as for every source.

**Read it once a month, in the first week.** Most posts arrive in the first two days. Later runs that month only need the comments newer than the last read.

**Skip what the post says plainly:** unpaid or volunteer groups, recruiters posting for undisclosed clients, US-only remote, and on-site roles without sponsorship. An email-only apply path is the user's to send (the run never sends mail as them); list it under "Needs you".
