---
name: seekter-init
description: Set up Seekter for a new candidate. Interviews the user one question at a time and writes profile/profile.md, profile/settings.json and profile/documents/. Use when the user runs /seekter-init, says "set up seekter", or when profile/profile.md is missing.
---

# /seekter-init — build the candidate profile

The goal is a complete `profile/profile.md` (from `templates/profile.md`) and `profile/settings.json` (from `templates/settings.json`). Every other Seekter skill reads only these files, so nothing may be guessed here: a value the user didn't give stays `ASK` in the profile, and a setting they didn't answer keeps the template's default.

## Rules for the interview

- **One question per message.** Short, plain, in the user's language. Offer choices with the question tool when the answer is one of a few options; free text otherwise.
- **Draft, then confirm.** If a CV (or a LinkedIn profile saved as PDF) is available, read it first and turn questions into confirmations ("Your CV says 8 years in this field. Correct?"). Never write a value from the CV without the user confirming it.
- **Skippable.** "Skip" or "later" writes `ASK` and moves on. Sensitive items (birth date, ethnicity, disability, gender, salary history) default to "prefer not to say" unless the user volunteers a value.
- **Resumable.** After each answered section, save progress to `profile/.init-state.json` (`{"done": ["identity", ...], "answers": {...}}`) and write what's known into `profile/profile.md`. Settings are written the moment they are answered, not at the end of a section. On restart, read the state file, say where you're resuming, and continue.
- **A half-finished setup still runs.** Only three profile values have no default: the application email, the default CV and the country. With those three in place, `/seekter-run` works; every other gap is asked when a form needs it, and every unanswered setting runs on its default.
- **Explain once why** at the start, right after the disclaimer: "I'll ask about 40 short questions in 9 groups. Everything stays in `profile/`, which git ignores."
- Don't ask what you can derive: timezone from city, ASCII fallback from name, E.164 phone from local number + country.

## Order

**Before anything else: the disclaimer.** Show this, as one message, before creating a folder or reading a file:

> Before we start: Seekter sends applications in your name and you are responsible for what they say, the terms of the job sites you use are yours to follow, and it runs on your own paid Claude plan with no guarantee of results. Details are in [DISCLAIMER.md](DISCLAIMER.md). Shall we continue? (yes / no)

- **No**, or anything that is not a yes: stop. Create nothing, write nothing, and say they can run `/seekter-init` again whenever they want.
- **Yes**: keep the date in `profile/.init-state.json` and write it to the profile's `Disclaimer accepted:` line when the profile is first written. Then run `python3 scripts/seekter.py settings > /dev/null`: it moves an older `profile/search.json` to `profile/settings.json` with its values intact, or copies `templates/settings.json` if there is neither, so a valid settings file is there before the first question. Never copy the template by hand: on an older setup that would shadow the user's `search.json`. Then continue.
- Don't paraphrase it into something softer or longer, and don't ask it again on a resumed run that already has the date.

0. **Setup check.** Create `profile/`, `profile/documents/`, `applications/`, `runs/` if missing. If `profile/profile.md` already exists, ask: update section by section, or start over (keep a copy as `profile/profile.backup-<date>.md`). An older setup may have `profile/search.json` instead of `profile/settings.json`: `python3 scripts/seekter.py settings` moves it across with its values unchanged, so don't rebuild it.
1. **Documents first** (they make the rest faster). Ask for the CV file path(s). Copy them into `profile/documents/` keeping the file name. Ask which is the default and whether another CV is for a different role type. Optional: portfolio PDF.
   Read the CV and pre-fill a draft of §1, §3 (employer, years, education), §8 (work history, facts, stack, certifications).
2. **Identity and contact (§1).** Name as written on the CV, first/last split, **the one email to use in every form**, phone, city + country + postcode, nationality, languages with levels, portfolio / LinkedIn / GitHub / other links, case-study URLs.
3. **Targets (§5).** Titles to apply for (suggest from CV), titles and seniority to exclude, management vs individual contributor, a one-paragraph recruiter statement (draft it, let them edit).
4. **Location and work model (§6).** Work authorization (where they may legally work today). For the home country: on-site / hybrid / remote allowed, and city exceptions. Relocation: yes/no, which countries in priority order, conditions (only with sponsorship?), regions never to relocate to. After these rules, propose plain phrases that might rule them out given their country and authorization (for example, "US only", "must be based in the US", "United States", "Canada only"). Let them add or remove phrases, one question at a time, and write the agreed list to `location_flags` in settings. Say once: these move a posting down and mark it; they never delete it. Quote the user's words in the profile.
5. **Money (§4).** Bands by employer region (monthly or annual, gross or net, currency). Minimum acceptable. Freelance rate if relevant. Salary history answer (a number or "prefer not to say").
6. **Standard answers (§3).** Notice period, contract types (employee / contractor / EOR), VAT or company registration, sponsorship need for remote vs relocation, non-compete, background questions, references, education search terms, AI-usage level, consents. Demographics last, with "prefer not to say" as the first option.
7. **Boundaries (§7).** Sectors never to apply to (offer: gambling/betting, adult, weapons/defense, tobacco, crypto, fast fashion, none). Companies to never apply to: quote their words in the profile and also write the names to `blocked_companies` in settings. Sectors to ask about first.
8. **Fact bank and stories (§8).** Walk through each job on the CV and ask for **2–3 concrete facts** each (a number, a product, a decision, a tool combination). Then ask for 3 short stories for behavioural questions: a time they were wrong, a conflict, a failure, a system they built outside work. Only facts and stories captured here may appear in applications.
9. **Voice (§9).** Show two short sample answers in different registers and ask which sounds like them. Ask for phrases they hate. Ask about punctuation habits (em dashes, exclamation marks) and US vs UK English.
10. **Search config.** Fill the search keys of `profile/settings.json`. **Every search value is theirs; the template ships them empty on purpose, so fill them from this candidate's field and geography rather than from an example.** Switches with a sensible default (`same_company_days`, `linkedin.read_limits`, `freehire.window_days`) stay as they are unless the user wants them changed; say what each one does in a line and move on.
    - freehire `queries` (their target titles, lowercase) and `categories`: don't guess the taxonomy — run `curl -sS -A seekter "https://freehire.me/api/v1/jobs/facets?q=<their main title>"` and pick the categories that actually carry their field, then show them the counts.
    - `regions` and `home_country` from §6; `languages` from §1.
    - **Filter calibration**, right after queries, categories and regions are set:
      1. Run `python3 scripts/freehire_sweep.py --sample 80` for real titles from their searches.
      2. Draft `title_keep` (families worth opening) and `title_drop` (wrong seniority, discipline and title collisions) from their target titles and exclusions. Ask which other industries share their titles: they know the collisions. Show the user words, never regex; use the template's worked example for its shape, not its words.
      3. Run `python3 scripts/seekter.py filter-test --titles runs/<date>/sample-titles.txt --keep '<draft>' --drop '<draft>'` and show both lists.
      4. Ask which titles are on the wrong side, one question per message. Adjust and re-run until they say both lists look right, at most four rounds; after four, save and note open doubts in the profile's §10. Quote their words.
      5. Only then write `title_keep` and `title_drop` to settings. If the sample fails because of the network, say so, write the draft, and tell them to run `filter-test` after the first sweep.
    - LinkedIn `searches`: one row per title family × geography (`keywords`, `location` for the alert, `geo_id` from the table in `reference/sources/linkedin.md`, `remote` per §6), plus a second row without the remote flag for any geography where they can work on-site. Each row is a job alert **they** create on LinkedIn, delivered by **Email**, and in `read` mode also a search Seekter runs. Apply the alert-quality rules from the same file while building the rows (no quotes, one row per title variant, EEA leaves out the UK and Switzerland, narrow beats broad because a digest mail shows only about six postings).
    - LinkedIn `mode`: ask, and say this first, in plain words: "Seekter never fills Easy Apply and never takes an action on LinkedIn. By default it doesn't open LinkedIn at all: it reads your job-alert emails and any links you give it. It can also read LinkedIn searches and job details itself, with a daily limit and pauses, and it turns that off at the first warning. That is still against LinkedIn's terms, and LinkedIn can restrict accounts that do it. The risk is to your account. Do you want it on?" `email` unless they say yes; on a yes, write `read` and quote their answer in the profile's §10.
    - `boards`: ask which boards they already read. A niche board for their own discipline beats every generic remote board (`reference/sources/_core.md`, the report card). Every board they name needs a file in `reference/sources/`; a board with no file has never been measured.
    Show the finished list and let them trim it.
11. **Tracker import (optional).** Ask whether they already track applications somewhere. Notion or any spreadsheet → export as CSV → `python3 scripts/import_csv.py <file>.csv --dry-run`, show the counts, then run it for real. This is what makes dedup work from day one.
12. **Finish.** Write the final `profile/profile.md` (fill §10–§12, set "Last updated"), delete `profile/.init-state.json`, run `python3 scripts/seekter.py index`, then print:
    - which fields are still `ASK` (each one line),
    - the browser prerequisite (Claude in Chrome extension, with their webmail logged in in the same Chrome),
    - the LinkedIn alerts to create by hand, one line each from `linkedin.searches`, delivered by Email,
    - that they can drop links they find into `profile/links.txt` (or paste them in chat) and those are filled first,
    - `python3 scripts/seekter.py filter-test` to re-check the title filters later,
    - the next command: `/seekter-run`.

## Writing the files

- `profile/profile.md`: copy `templates/profile.md`, replace every `{{…}}`. Remove table rows that don't apply rather than leaving placeholders. Leave no `{{` in the finished file (check with grep).
- `profile/settings.json`: valid JSON, and `python3 scripts/seekter.py settings --check` passes. It lists the settings still on their defaults; read that list out so the user knows what they can change by hand.
- Keep the user's exact words for rules they state; quote them in the profile, as they're the ground truth later.
