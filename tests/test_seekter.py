#!/usr/bin/env python3
"""Tests for scripts/seekter.py. Standard library only: python3 -m unittest discover tests

The tracker CLI is the one part of this kit that can lose an application. Every
case below is either a promise the README makes or a bug that already cost a
duplicate submission, and the comment says which.

The pure functions are imported directly. Everything that touches the tracker
runs the real script as a subprocess against a throwaway repository, because
ROOT is derived from the script's own location and the exit code of `check` is
part of the contract.
"""
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "seekter.py"
TODAY = dt.date.today().isoformat()


def _import():
    spec = importlib.util.spec_from_file_location("seekter_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sk = _import()


# ---------------------------------------------------------------- job identity

class JobKeyTests(unittest.TestCase):
    """job_key is the load-bearing field: it is what makes the same posting
    reached through three different sites one record instead of three."""

    def test_empty_url_has_no_key(self):
        self.assertEqual(sk.job_key(""), "")
        self.assertEqual(sk.job_key(None), "")

    def test_linkedin_path_and_query_forms_agree(self):
        # The same job arrives as /jobs/view/<id> from the API and as
        # ?currentJobId=<id> from the search page. They must key the same.
        a = sk.job_key("https://www.linkedin.com/jobs/view/4468710729/")
        b = sk.job_key("https://www.linkedin.com/jobs/search/?currentJobId=4468710729&keywords=x")
        self.assertEqual(a, "linkedin:4468710729")
        self.assertEqual(a, b)

    def test_greenhouse_forms(self):
        self.assertEqual(sk.job_key("https://boards.greenhouse.io/acme/jobs/4567890"),
                         "greenhouse:4567890")
        self.assertEqual(sk.job_key("https://acme.com/careers?gh_jid=4567890"),
                         "greenhouse:4567890")

    def test_uuid_anywhere_in_the_path_wins_and_is_lowercased(self):
        upper = "https://jobs.ashbyhq.com/acme/1E548ADA-1111-2222-3333-444455556666"
        lower = "https://jobs.ashbyhq.com/acme/1e548ada-1111-2222-3333-444455556666/application"
        self.assertEqual(sk.job_key(upper), "uuid:1e548ada-1111-2222-3333-444455556666")
        self.assertEqual(sk.job_key(upper), sk.job_key(lower))

    def test_breezy_keys_on_the_id_not_the_title_slug(self):
        # Measured 25 Sept: an employer renamed a Breezy posting without opening a
        # new one. Keying on the whole path made the rename look like a new job, it
        # passed dedup as NEW, and only Breezy's own server caught the second form.
        before = "https://acme.breezy.hr/p/ff94f3182ac2-senior-product-designer"
        after = "https://acme.breezy.hr/p/ff94f3182ac2-senior-product-design-engineer"
        self.assertEqual(sk.job_key(before), "breezy:ff94f3182ac2")
        self.assertEqual(sk.job_key(before), sk.job_key(after))

    def test_hacker_news_posts_key_on_the_comment_id(self):
        # Measured 9 Oct: every "Who is hiring?" post lives at /item?id=<n>, so the
        # whole thread keyed as one posting and a skipped post blocked an application.
        a = sk.job_key("https://news.ycombinator.com/item?id=49924889")
        b = sk.job_key("https://news.ycombinator.com/item?id=49932275")
        self.assertEqual(a, "hn:49924889")
        self.assertNotEqual(a, b)

    def test_indeed_keeps_the_id_in_the_query(self):
        # Without this every Indeed posting collapses to "<host>/viewjob" and the
        # first one tracked makes all the others look like duplicates.
        one = sk.job_key("https://uk.indeed.com/viewjob?jk=abc123def456")
        two = sk.job_key("https://uk.indeed.com/viewjob?jk=999888777666")
        self.assertEqual(one, "indeed:abc123def456")
        self.assertNotEqual(one, two)

    def test_generic_id_query_param_on_a_careers_site(self):
        # Measured 24 Sept: careers.celonis.com had two design roles whose paths
        # were identical and whose ids lived in a query param.
        one = sk.job_key("https://careers.example.com/job-detail?jobId=1234567")
        two = sk.job_key("https://careers.example.com/job-detail?jobId=7654321")
        self.assertEqual(one, "careers.example.com:1234567")
        self.assertNotEqual(one, two)

    def test_apply_suffix_and_www_do_not_create_a_second_record(self):
        plain = sk.job_key("https://www.example.com/careers/senior-designer")
        applying = sk.job_key("https://example.com/careers/senior-designer/apply")
        self.assertEqual(plain, applying)
        self.assertEqual(plain, "example.com/careers/senior-designer")

    def test_a_url_with_no_path_falls_back_rather_than_keying_on_the_host(self):
        # Keying on the host alone would make every posting on that site one record.
        a = sk.job_key("https://jobs.example.com/#/postings/aaa")
        b = sk.job_key("https://jobs.example.com/#/postings/bbb")
        self.assertNotEqual(a, b)
        self.assertNotEqual(sk.job_key("https://jobs.example.com/?id=aaa"),
                            sk.job_key("https://jobs.example.com/?id=bbb"))


# ------------------------------------------------------------- normalisation

class NormaliseTests(unittest.TestCase):

    def test_slug_folds_letters_that_nfkd_alone_would_delete(self):
        # NFKD only folds a letter that decomposes into base + combining mark.
        # A letter that is its own base character gets deleted instead, so the
        # dotless ı disappeared: "Desıgn Ateliér" became "desgn-atelier".
        self.assertEqual(sk.slug("Desıgn Ateliér"), "design-atelier")
        self.assertEqual(sk.slug("İnfo Über"), "info-uber")
        for word, want in [("Łódź", "lodz"), ("Ørsted", "orsted"), ("Straße", "strasse"),
                           ("Æther", "aether"), ("Đuro", "duro")]:
            self.assertEqual(sk.slug(word), want, word)

    def test_slug_does_not_fold_two_different_employers_onto_one_string(self):
        # The same-company check in `check` compares slugs, so a deletion here
        # is not only cosmetic: before the fix both of these were "lght".
        self.assertNotEqual(sk.slug("Lıght"), sk.slug("Lght"))

    def test_slug_truncates_without_a_trailing_dash(self):
        self.assertEqual(sk.slug("a" * 60), "a" * 40)
        self.assertFalse(sk.slug("word " + "x" * 60, 6).endswith("-"))
        self.assertEqual(sk.slug(""), "x")  # never an empty filename fragment

    def test_norm_lowercases_and_hyphenates_enums(self):
        self.assertEqual(sk.norm("Company site"), "company-site")
        self.assertEqual(sk.norm("  Other  Board "), "other-board")
        self.assertEqual(sk.norm(None), "")

    def test_ats_is_a_function_of_the_url(self):
        self.assertEqual(sk.ats_from_url("https://jobs.ashbyhq.com/acme/x"), "ashby")
        self.assertEqual(sk.ats_from_url("https://jobs.lever.co/acme/x"), "lever")
        self.assertEqual(sk.ats_from_url("https://acme.com/careers"), "")

    def test_an_ats_name_stored_as_source_moves_to_ats(self):
        # A common mix-up in hand-kept trackers: the column says where you applied
        # through, not where you found it. Source becomes the generic "board".
        r = {"source": "Greenhouse", "url": "https://boards.greenhouse.io/acme/jobs/1"}
        sk.fix_meta(r)
        self.assertEqual(r["ats"], "greenhouse")
        self.assertEqual(r["source"], "board")

    def test_other_board_is_just_board(self):
        r = {"source": "Other board"}
        sk.fix_meta(r)
        self.assertEqual(r["source"], "board")

    def test_fix_meta_fills_the_key_and_reports_only_real_changes(self):
        r = {"url": "https://www.linkedin.com/jobs/view/123456789/"}
        changed = sk.fix_meta(r)
        self.assertEqual(r["job_key"], "linkedin:123456789")
        self.assertIn("job_key", changed)
        # Running it again must be a no-op, or `normalize` reports work forever.
        self.assertEqual(sk.fix_meta(dict(r)), {})


class ScreeningTests(unittest.TestCase):
    def test_screen_reports_exclusions_and_literal_location_phrases(self):
        cfg = dict(blocked_companies=["Flex"], location_flags=["US only", "C++"])
        keep, drop = sk.title_filters(dict(title_keep="designer", title_drop="interior"))
        self.assertEqual(sk.screen("Designer", "Flex Ltd", "Remote", cfg, keep, drop), ("skip", "blocked company: Flex"))
        self.assertEqual(sk.screen("Designer", "WorkFlex", "Remote", cfg, keep, drop), ("consider", ""))
        self.assertEqual(sk.screen("Interior Designer", "Acme", "", cfg, keep, drop), ("skip", "title_drop: Interior"))
        self.assertEqual(sk.screen("Engineer", "Acme", "", cfg, keep, drop), ("skip", "title_keep: no match"))
        self.assertEqual(sk.screen("Designer", "Acme", "Remote, us ONLY", cfg, keep, drop), ("flag", "location: US only"))
        self.assertEqual(sk.screen("Designer", "Acme", "C++ required", cfg, keep, drop), ("flag", "location: C++"))
        self.assertEqual(sk.screen("Designer", "Acme", "C required; AUS only", cfg, keep, drop), ("consider", ""))


    def test_phrase_lists_ignore_empty_entries_and_accept_none(self):
        self.assertEqual(sk.blocked_company("Acme", dict(blocked_companies=None)), "")
        self.assertEqual(sk.location_flag("Remote", dict(location_flags=None)), "")
        self.assertEqual(sk.location_flag("Remote", dict(location_flags=["", "  "])), "")
        self.assertEqual(sk.location_flag("US only", dict(location_flags=["  US only  "])), "location: US only")
        for key in ("blocked_companies", "location_flags"):
            for value in ("US only", 3, {}, [3]):
                with self.assertRaisesRegex(SystemExit, key + " must be a list of names/phrases"):
                    sk._phrase_list({key: value}, key)


class TableTests(unittest.TestCase):
    """A skip row is a markdown table cell, so a reason containing a pipe has to
    survive being read and written repeatedly."""

    def test_clean_text_removes_terminal_controls_and_bidi(self):
        self.assertEqual(sk.clean_text("\x1b[31mRed\x1b[0m"), "Red")
        self.assertEqual(sk.clean_text("A\x00\x85\u202e\u2066B\t  C\nD"), "A B C D")
        self.assertEqual(sk.clean_text("  ABC DEF  ", 4), "ABC ")
        self.assertEqual(sk.clean_text(123), "123")
        self.assertEqual(sk.clean_text("Product\nDesigner"), "Product Designer")
        self.assertEqual(sk.clean_text("A\t\n\r\v\f\x85\u2028\u2029B"), "A B")

    def test_markdown_urls_encode_delimiters_only_for_display(self):
        self.assertEqual(sk._md_url("javascript:alert(1)"), "")
        self.assertEqual(sk._md_url("https://example.com/a (b)<c>[d]\\|"), "https://example.com/a%20%28b%29%3Cc%3E%5Bd%5D%5C%7C")

    def test_escaping_a_cell_is_idempotent(self):
        once = sk._cell("remote | EU only")
        self.assertEqual(once, r"remote \| EU only")
        self.assertEqual(sk._cell(once), once)  # no second backslash

    def test_a_reason_with_a_pipe_survives_a_round_trip(self):
        original = "country list: DE | IT | PT, not TR"
        row = "| " + " | ".join(sk._cell(x) for x in
                                (TODAY, "Acme", "Designer", original, "board", "", "k")) + " |"
        cells = sk._split_row(row)
        self.assertEqual(cells[3], original)
        again = sk._cell(cells[3])
        self.assertEqual(sk._split_row("| " + again + " |")[0], original)

    def test_newlines_are_flattened_so_a_cell_cannot_break_the_table(self):
        self.assertEqual(sk._cell("two\nlines"), "two lines")


# --------------------------------------------------------------- the tracker

class TrackerTests(unittest.TestCase):
    """The real script, run against a throwaway repository."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="seekter-test-"))
        (self.tmp / "scripts").mkdir()
        shutil.copy2(SCRIPT, self.tmp / "scripts" / "seekter.py")
        (self.tmp / "applications").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args, stdin=None):
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "seekter.py"), *args],
                           input=stdin, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def add(self, **kw):
        args = []
        for k, v in kw.items():
            args += ["--" + k.replace("_", "-"), v]
        rc, out, err = self.run_cli("add", *args)
        self.assertEqual(rc, 0, f"add failed: {err}")
        return self.tmp / out.strip().splitlines()[0]

    def skips_table(self, month=None):
        return (self.tmp / "applications" / (month or TODAY[:7]) / "skipped.md").read_text()

    def test_add_cleans_role_and_keeps_table_columns_and_no_temp_files(self):
        path = self.add(company="Acme", role="Designer\x1b[2J](javascript:x) | Remote", status="applied",
                        url="javascript:x", notes="Line\nTwo\t\x01\u202e", answers="\x1b[31mAnswer\x1b[0m")
        self.assertNotIn("\x1b", path.read_text())
        self.assertIn("Line\nTwo\t", path.read_text())
        index = (self.tmp / "applications" / "README.md").read_text()
        self.assertNotRegex(index, r"(?<!\\)(?:\\\\)*\]\(javascript:")
        row = next(x for x in index.splitlines() if "Designer" in x)
        self.assertEqual(len(sk._split_row(row)), 6)
        self.assertEqual({p.name for p in path.parent.iterdir()}, {path.name, "README.md"})

    def test_role_backslash_cannot_unescape_link_text(self):
        self.add(company="Acme", role=r"x\](https://evil)", url="https://example.com/role")
        index = (self.tmp / "applications" / "README.md").read_text()
        self.assertIn(r"[x\\\](https://evil)]", index)
        self.assertNotRegex(index, r"(?<!\\)(?:\\\\)*\]\(https://evil\)")

    def test_posting_url_escaping_keeps_record_identity(self):
        url = "https://a.example/)[click](javascript:alert(1)"
        path = self.add(company="Acme", role="Designer", url=url)
        self.assertIn("url: " + url, path.read_text())
        index = (self.tmp / "applications" / "README.md").read_text()
        self.assertNotIn("](javascript:", index)
        self.assertIn("%29%5Bclick%5D%28javascript:alert%281%29", index)
        skip_url = url + "/skip"
        self.add(company="Acme", role="Designer", status="skipped", url=skip_url)
        self.assertNotIn("](javascript:", self.skips_table())
        skip_path = self.tmp / "applications" / TODAY[:7] / "skipped.md"
        record = sk.read_skips(skip_path)[0]
        self.assertEqual(record["url"], skip_url)
        self.assertEqual(sk.job_key(record["url"]), sk.job_key(skip_url))
        encoded_url = "https://a.example/already%29encoded"
        self.add(company="Other", role="Designer", status="skipped", url=encoded_url)
        encoded = next(r for r in sk.read_skips(skip_path) if r["company"] == "Other")
        self.assertEqual(encoded["url"], encoded_url)
        self.assertEqual(sk.job_key(encoded["url"]), sk.job_key(encoded_url))

    def test_filter_test_date_validation_matches_queue(self):
        for date in ("../outside", "2026-02-30", "20261012"):
            rc, out, err = self.run_cli("filter-test", "--date", date, "--titles", "-", "--keep", "designer", stdin="Designer\n")
            self.assertEqual(rc, 1)
            self.assertIn("problem: --date must be YYYY-MM-DD", out + err)

    def test_angle_brackets_do_not_make_tracker_autolinks(self):
        self.add(company="<https://evil>", role="<javascript:alert(1)>", url="https://example.com/autolink")
        index = (self.tmp / "applications" / "README.md").read_text()
        self.assertNotIn("<https://evil>", index)
        self.assertNotIn("<javascript:", index)
        self.assertIn("&lt;https://evil&gt;", index)
        self.assertIn("&lt;javascript:alert(1)&gt;", index)

    def test_queue_uses_run_date_for_posted_freshness_and_escapes_location(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        (profile / "settings.json").write_text(json.dumps(dict(title_keep="designer")))
        date = "2020-01-10"
        run = self.tmp / "runs" / date
        run.mkdir(parents=True)
        (run / "freehire.json").write_text(json.dumps([
            dict(company="Age", title="Designer", url="https://example.com/age", age=2)]))
        (run / "employers.json").write_text(json.dumps([
            dict(company="Posted", title="Designer", location="<https://evil>", url="https://example.com/posted", posted="2020-01-09")]))
        rc, out, err = self.run_cli("queue", "--date", date)
        self.assertEqual(rc, 0, err)
        text = (run / "queue.md").read_text()
        rows = [sk._split_row(x) for x in text.splitlines() if x.startswith("| ")][1:]
        self.assertEqual([r[3] for r in rows], ["Posted", "Age"])
        self.assertNotIn("<https://evil>", text)
        self.assertEqual(rows[0][5], "&lt;https://evil&gt;")

    def test_move_refuses_profile_and_symlink_outside_tracker(self):
        profile = self.tmp / "profile" / "profile.md"
        profile.parent.mkdir()
        profile.write_bytes(b"Profile remains intact\n")
        before = profile.read_bytes()
        proc = subprocess.run([sys.executable, str(self.tmp / "scripts" / "seekter.py"),
                               "move", "../profile/profile.md", "applied"], cwd=self.tmp / "scripts",
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("refusing:", proc.stderr)
        self.assertEqual(profile.read_bytes(), before)
        month = self.tmp / "applications" / TODAY[:7]
        month.mkdir()
        link = month / "escape.md"
        link.symlink_to(profile)
        rc, _, err = self.run_cli("move", str(link), "applied")
        self.assertNotEqual(rc, 0)
        self.assertIn("refusing:", err)
        self.assertEqual(profile.read_bytes(), before)

    def test_queue_filters_deduplicates_orders_and_leaves_tracker_unchanged(self):
        self.add(company="Acme", role="Designer", url="https://example.com/tracked")
        self.add(company="Oldco", role="Designer", url="https://example.com/old", status="closed")
        profile = self.tmp / "profile"
        profile.mkdir()
        (profile / "settings.json").write_text(json.dumps(dict(title_keep="designer", title_drop="interior")))
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        (run / "freehire.json").write_text(json.dumps([
            dict(company="Acme", title="Designer", url="https://example.com/tracked", age=0),
            dict(company="Fresh", title="Designer", url="https://example.com/new", age=1),
            dict(company="Acme", title="Designer", url="https://example.com/hold", age=0),
            dict(company="Drop", title="Interior Designer", url="https://example.com/drop", age=0)]))
        (run / "employers.json").write_text(json.dumps([
            dict(company="Fresh", title="Designer", url="https://example.com/new"),
            dict(company="Oldco", title="Designer", url="https://example.com/same", posted=TODAY)]))
        (run / "hn.json").write_text(json.dumps([
            dict(header="HNco | Remote", url="https://news.ycombinator.com/item?id=123"),
            dict(header="Dropco | Interior | Remote", url="https://news.ycombinator.com/item?id=125"),
            dict(header="Designer", url="https://news.ycombinator.com/item?id=124")]))
        before = {p: p.read_bytes() for p in (self.tmp / "applications").rglob("*") if p.is_file()}
        rc, out, err = self.run_cli("queue")
        self.assertEqual(rc, 0, err)
        self.assertIn("queue: 5 postings (3 new, 1 same company, 1 on hold), 2 already tracked or duplicate, 0 flagged, 0 blocked company", out)
        table = [sk._split_row(x) for x in (run / "queue.md").read_text().splitlines() if x.startswith("| ")][1:]
        self.assertEqual([x[1] for x in table], ["NEW", "NEW", "NEW", "SAMECO", "HOLD"])
        self.assertEqual([x[3] for x in table[:3]], ["Fresh", "HNco", ""])
        self.assertEqual(table[1][4], "HNco | Remote")
        self.assertEqual(before, {p: p.read_bytes() for p in (self.tmp / "applications").rglob("*") if p.is_file()})

    def test_queue_preserves_hand_section_verbatim(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        (profile / "settings.json").write_text(json.dumps(dict(title_keep="designer")))
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        (run / "freehire.json").write_text("[]")
        self.assertEqual(self.run_cli("queue")[0], 0)
        queue = run / "queue.md"
        self.assertIn("## Added by hand\n\nLinkedIn, board", queue.read_text())
        hand = "## Added by hand\r\n\r\n| links | Designer | https://example.com/manual | A | hold reason |\r\n"
        queue.write_bytes(queue.read_bytes().split(b"## Added by hand")[0] + hand.encode())
        self.assertEqual(self.run_cli("queue")[0], 0)
        self.assertTrue(queue.read_bytes().endswith(hand.encode()))
        self.assertIn("HN rows were title-matched on the whole post", queue.read_text())

    def test_move_preserves_record_permissions(self):
        path = self.add(company="Acme", role="Designer", url="https://example.com/mode")
        path.chmod(0o644)
        rc, _, err = self.run_cli("move", str(path), "interviewing")
        self.assertEqual(rc, 0, err)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_company_and_posting_cannot_break_index_table(self):
        self.add(company="[x](https://evil)", role="Designer", url="https://x.example/?a=1|2", location_fit="A|B")
        index = (self.tmp / "applications" / "README.md").read_text()
        self.assertNotRegex(index, r"(?<!\\)(?:\\\\)*\]\(https://evil\)")
        row = next(x for x in index.splitlines() if "Designer" in x)
        self.assertEqual(len(sk._split_row(row)), 6)
        self.assertIn("https://x.example/?a=1%7C2", row)

    def test_queue_empty_urls_are_kept_and_filtered_rows_do_not_claim_keys(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        (profile / "settings.json").write_text(json.dumps(dict(title_keep="designer", title_drop="interior")))
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        (run / "freehire.json").write_text(json.dumps([
            dict(company="Acme", title="Interior Designer", url="https://example.com/shared"),
            dict(company="Acme", title="Designer", url=""),
            dict(company="Other", title="Designer", url="")]))
        (run / "employers.json").write_text(json.dumps([
            dict(company="Acme", title="Designer", url="https://example.com/shared")]))
        rc, out, err = self.run_cli("queue")
        self.assertEqual(rc, 0, err)
        self.assertIn("queue: 3 postings (3 new, 0 same company, 0 on hold), 0 already tracked or duplicate, 0 flagged, 0 blocked company", out)

    def test_queue_invalid_sweep_data_reports_problem_without_traceback(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        (profile / "settings.json").write_text(json.dumps(dict(title_keep="designer")))
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        for data in ("not json", "{}", "[1]", "[null]"):
            (run / "freehire.json").write_text(data)
            rc, out, err = self.run_cli("queue")
            self.assertEqual(rc, 1)
            self.assertIn(f"problem: runs/{TODAY}/freehire.json is not valid", out)
            self.assertNotIn("Traceback", err)
            self.assertFalse((run / "queue.md").exists())

    def test_filter_test_file_overrides_stdin_and_no_writes(self):
        titles = self.tmp / "titles.txt"
        titles.write_text("Designer\ndesigner\nInterior Designer\nEngineer\n")
        before = {p: p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}
        rc, out, err = self.run_cli("filter-test", "--titles", str(titles), "--keep", "designer", "--drop", "interior")
        self.assertEqual(rc, 0, err)
        self.assertIn("WOULD CONSIDER (1)\n  ✓ Designer", out)
        self.assertIn("WOULD SKIP (2)\n  ✗ Engineer   ← no keep match\n  ✗ Interior Designer   ← Interior", out)
        self.assertEqual(before, {p: p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()})
        rc, out, err = self.run_cli("filter-test", "--titles", "-", "--keep", "designer", "--drop", "", stdin="Designer\n")
        self.assertEqual(rc, 0, err)
        self.assertIn("WOULD CONSIDER (1)", out)
        rc, out, err = self.run_cli("filter-test", "--titles", str(titles), "--keep", "[")
        self.assertEqual(rc, 1)
        self.assertIn("problem:", out + err)

    def test_filter_test_does_not_migrate_legacy_settings(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        legacy = profile / "search.json"
        legacy.write_text(json.dumps(dict(title_keep="designer")))
        before = legacy.read_bytes()
        rc, out, err = self.run_cli("filter-test", "--titles", "-", stdin="Designer\n")
        self.assertEqual(rc, 0, err)
        self.assertEqual(legacy.read_bytes(), before)
        self.assertFalse((profile / "settings.json").exists())

    def test_filter_test_sweeps_and_title_drops_only(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        (profile / "settings.json").write_text(json.dumps(dict(title_keep="designer", title_drop="interior")))
        rc, out, _ = self.run_cli("filter-test")
        self.assertEqual(rc, 1)
        self.assertIn("problem: no titles to test", out)
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        (run / "freehire.json").write_text(json.dumps([dict(title="Designer")]))
        (run / "hn.json").write_text(json.dumps([dict(header="Acme | Designer")]))
        (run / "hn-dropped.json").write_text(json.dumps([dict(title="HN Interior Designer", reason="title_drop: Interior")]))
        (run / "employers-dropped.json").write_text(json.dumps([
            dict(title="Interior Designer", reason="title_drop: Interior"),
            dict(title="Engineer", reason="title_keep: no match"),
            dict(title="Sector Designer", reason="sector: adult")]))
        rc, out, err = self.run_cli("filter-test")
        self.assertEqual(rc, 0, err)
        self.assertIn("WOULD CONSIDER (1)", out)
        self.assertIn("WOULD SKIP (2)", out)
        self.assertNotIn("Sector Designer", out)
        self.assertNotIn("Acme | Designer", out)
        self.assertNotIn("HN Interior Designer", out)
        self.assertEqual(out.count("(HN posts are matched on the whole post by the sweep, so they are not part of this test)"), 1)

    def test_queue_flags_blocks_escapes_and_preserves_hand_section(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        (profile / "settings.json").write_text(json.dumps(dict(title_keep="designer", blocked_companies=["Blocked"], location_flags=["US only"])))
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        (run / "freehire.json").write_text(json.dumps([
            dict(company="Blocked", title="Designer", url="https://example.com/blocked"),
            dict(company="Flagged", title="Designer", location="US only", url="https://example.com/flagged", age=0),
            dict(company="[Acme](https://evil)", title="Designer [x](https://evil)", location="[apply here](https://evil)", url="javascript:x", age=9),
            dict(company="Carried", title="Designer", flag="location: [previous flag](https://evil)", url="https://a.example/)[click](javascript:alert(1)", age=1)]))
        (run / "queue.md").write_text("old\n## Added by hand\n\nmanual candidate\n")
        rc, out, err = self.run_cli("queue")
        self.assertEqual(rc, 0, err)
        self.assertIn("2 flagged, 1 blocked company", out)
        text = (run / "queue.md").read_text()
        self.assertIn("| Location | Flag | Apply URL |", text)
        self.assertTrue(text.endswith("## Added by hand\n\nmanual candidate\n"))
        self.assertNotRegex(text, r"(?<!\\)(?:\\\\)*\]\(https://evil\)")
        rows = [sk._split_row(x) for x in text.splitlines() if x.startswith("| ")][1:]
        self.assertEqual(len(rows), 3)
        self.assertEqual([r[3] for r in rows[1:]], ["Flagged", "Carried"])
        self.assertEqual(rows[0][-1], "")
        self.assertEqual(rows[0][5], r"\[apply here\](https://evil)")
        self.assertEqual(rows[2][6], r"location: \[previous flag\](https://evil)")
        self.assertNotIn("](javascript:", text)
        self.assertEqual(rows[2][7], sk._md_url("https://a.example/)[click](javascript:alert(1)"))
        self.assertTrue(all(len(r) == 8 for r in rows))

    def test_settings_check_rejects_screening_settings(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        for key in ("location_flags", "blocked_companies"):
            for value in ("US only", [""], [3]):
                (profile / "settings.json").write_text(json.dumps({key: value}))
                rc, out, _ = self.run_cli("settings", "--check")
                self.assertEqual(rc, 1)
                self.assertIn(f"problem: {key} must be a list of non-empty strings", out)

    def test_skip_non_http_link_is_plain_cleaned_text(self):
        self.add(company="Acme", role="Designer", status="skipped", url="javascript:x")
        self.assertIn("| javascript:x |", self.skips_table())
        self.assertNotIn("<javascript:x>", self.skips_table())

    def test_queue_without_sweep_output(self):
        rc, out, _ = self.run_cli("queue")
        self.assertEqual(rc, 1)
        self.assertIn("problem: no sweep output", out)

    # -- dedup ------------------------------------------------------------

    def test_check_is_new_then_duplicate_after_add(self):
        url = "https://jobs.ashbyhq.com/acme/1e548ada-1111-2222-3333-444455556666"
        rc, out, _ = self.run_cli("check", url, "--company", "Acme")
        self.assertEqual(rc, 0)
        self.assertIn("NEW", out)

        self.add(company="Acme", role="Senior Designer", url=url, status="applied")

        rc, out, _ = self.run_cli("check", url, "--company", "Acme")
        self.assertEqual(rc, 1, "an already-tracked posting must exit 1")
        self.assertIn("DUPLICATE", out)

    def test_dedup_matches_across_the_url_it_was_first_stored_under(self):
        # Measured 23 Sept: a role was submitted a third time because the sweep
        # deduped LinkedIn ids while the record was keyed on the Ashby UUID.
        # `check` runs on the resolved apply URL, and must match either form.
        stored = "https://jobs.ashbyhq.com/acme/1e548ada-1111-2222-3333-444455556666"
        self.add(company="Acme", role="Designer", url=stored)
        same_job_other_path = ("https://jobs.ashbyhq.com/acme/"
                               "1E548ADA-1111-2222-3333-444455556666/application")
        rc, out, _ = self.run_cli("check", same_job_other_path)
        self.assertEqual(rc, 1)
        self.assertIn("DUPLICATE", out)

    def test_a_skipped_posting_is_never_evaluated_twice(self):
        # The README promises dedup reads the skip table too.
        url = "https://acme.com/careers/designer"
        self.add(company="Acme", role="Designer", url=url, status="skipped",
                 notes="the country list leaves out yours")
        self.assertIn("the country list leaves out yours", self.skips_table())
        rc, out, _ = self.run_cli("check", url)
        self.assertEqual(rc, 1)
        self.assertIn("DUPLICATE", out)

    def test_same_company_different_role_is_not_a_duplicate(self):
        self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                 status="applied", applied="2020-01-01")
        rc, out, _ = self.run_cli("check", "https://acme.com/careers/7654321", "--company", "Acme")
        self.assertEqual(rc, 0)
        self.assertIn("NEW", out)
        self.assertIn("same company, other role", out)

    # -- one application per company --------------------------------------

    def test_a_second_role_at_the_same_company_within_the_window_is_held(self):
        # Greenhouse can auto-reject further applications to a department inside
        # a window and tell nobody. Measured 2 Oct: two roles at one company went
        # out the same afternoon.
        self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                 status="applied")
        rc, out, _ = self.run_cli("check", "https://acme.com/careers/7654321", "--company", "Acme")
        self.assertEqual(rc, 2, "a recent application at the same company must exit 2")
        self.assertIn("HOLD", out)

    def test_a_skip_at_the_same_company_holds_nothing(self):
        self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                 status="skipped", notes="wrong country")
        rc, out, _ = self.run_cli("check", "https://acme.com/careers/7654321", "--company", "Acme")
        self.assertEqual(rc, 0)
        self.assertNotIn("HOLD", out)

    def test_a_live_interview_holds_the_company_whatever_the_date(self):
        self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                 status="interviewing", applied="2020-01-01")
        rc, out, _ = self.run_cli("check", "https://acme.com/careers/7654321", "--company", "Acme")
        self.assertEqual(rc, 2)

    def test_the_window_comes_from_the_profile(self):
        (self.tmp / "profile").mkdir()
        (self.tmp / "profile" / "settings.json").write_text('{"same_company_days": 0}')
        self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                 status="applied", applied="2020-01-01")
        rc, _, _ = self.run_cli("check", "https://acme.com/careers/7654321", "--company", "Acme")
        self.assertEqual(rc, 0)

    def test_a_name_inside_another_word_is_not_the_same_company(self):
        # Measured 5 and 6 Oct: "telli" matched Intellias and "Flex" matched WorkFlex,
        # and both were held under the 30-day rule for employers never applied to.
        self.add(company="Intellias", role="Designer", url="https://acme.com/careers/1234567",
                 status="applied")
        self.add(company="WorkFlex", role="Designer", url="https://b.com/careers/2345678",
                 status="applied")
        for name, url in (("telli", "https://c.com/careers/3456789"),
                          ("Flex", "https://d.com/careers/4567890")):
            rc, out, _ = self.run_cli("check", url, "--company", name)
            self.assertEqual(rc, 0, f"{name} must not be held: {out}")
            self.assertNotIn("same company", out)
        rc, out, _ = self.run_cli("check-many", stdin="https://c.com/careers/3456789 | telli\n")
        self.assertTrue(out.startswith("NEW"), out)

    def test_a_whole_word_of_the_company_name_still_matches(self):
        # The fix must not lose the matches that were right: "Hays" is "Hays Poland".
        self.add(company="Hays Poland", role="Designer", url="https://acme.com/careers/1234567",
                 status="applied")
        rc, out, _ = self.run_cli("check", "https://acme.com/careers/7654321", "--company", "Hays")
        self.assertEqual(rc, 2)
        self.assertIn("HOLD", out)

    # -- settings ------------------------------------------------------------

    def with_template(self):
        (self.tmp / "templates").mkdir(exist_ok=True)
        shutil.copy2(SCRIPT.parent.parent / "templates" / "settings.json",
                     self.tmp / "templates" / "settings.json")

    def test_an_old_search_json_is_moved_not_rewritten(self):
        # Setups from before the settings file have profile/search.json. Its values
        # are the user's; the first read moves the file and keeps every one of them.
        (self.tmp / "profile").mkdir()
        (self.tmp / "profile" / "search.json").write_text('{"same_company_days": 0, "x": [1]}')
        self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                 status="applied", applied="2020-01-01")
        rc, _, err = self.run_cli("check", "https://acme.com/careers/7654321", "--company", "Acme")
        self.assertEqual(rc, 0)
        self.assertFalse((self.tmp / "profile" / "search.json").exists())
        self.assertEqual(json.loads((self.tmp / "profile" / "settings.json").read_text()),
                         {"same_company_days": 0, "x": [1]})

    def test_a_missing_settings_file_is_created_from_the_template(self):
        self.with_template()
        rc, out, _ = self.run_cli("settings")
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "profile" / "settings.json").exists())
        self.assertEqual(json.loads(out)["linkedin"]["mode"], "email")

    def test_a_key_left_out_runs_on_the_default(self):
        # A half-finished /seekter-init leaves a partial file; it must still run.
        self.with_template()
        (self.tmp / "profile").mkdir()
        (self.tmp / "profile" / "settings.json").write_text('{"linkedin": {"mode": "read"}}')
        rc, out, _ = self.run_cli("settings")
        cfg = json.loads(out)
        self.assertEqual(cfg["linkedin"]["mode"], "read")
        self.assertEqual(cfg["linkedin"]["read_limits"]["searches_per_run"], 15)
        self.assertEqual(cfg["same_company_days"], 30)

    def test_check_stops_only_on_values_with_no_default(self):
        self.with_template()
        rc, out, _ = self.run_cli("settings", "--check")
        self.assertEqual(rc, 1)
        self.assertIn("profile/profile.md is missing", out)
        (self.tmp / "profile" / "documents").mkdir(parents=True)
        (self.tmp / "profile" / "profile.md").write_text("Email: {{EMAIL}}\nNotice: {{NOTICE}}\n")
        rc, out, _ = self.run_cli("settings", "--check")
        self.assertEqual(rc, 1)
        self.assertIn("application email", out)
        self.assertIn("no CV file", out)
        self.assertNotIn("NOTICE", out, "an optional gap is asked later, it does not stop the run")
        (self.tmp / "profile" / "profile.md").write_text("Email: a@b.c\nNotice: {{NOTICE}}\n")
        (self.tmp / "profile" / "documents" / "cv.pdf").write_bytes(b"%PDF")
        rc, out, _ = self.run_cli("settings", "--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("using defaults for", out)

    def test_an_old_file_beside_a_new_one_is_reported_not_lost(self):
        # Found in an end-to-end test on 6 Oct: init copied the template first, after
        # which the old search.json was never moved and its values silently ignored.
        (self.tmp / "profile").mkdir()
        (self.tmp / "profile" / "search.json").write_text('{"same_company_days": 7}')
        (self.tmp / "profile" / "settings.json").write_text("{}")
        rc, _, err = self.run_cli("settings")
        self.assertEqual(rc, 0)
        self.assertIn("both profile/settings.json and profile/search.json exist", err)
        self.assertTrue((self.tmp / "profile" / "search.json").exists())

    def test_a_broken_settings_file_is_reported_not_swallowed(self):
        self.with_template()
        (self.tmp / "profile").mkdir()
        for bad in ("{not json", "[1, 2]", '{"linkedin": "read"}'):
            (self.tmp / "profile" / "settings.json").write_text(bad)
            rc, out, err = self.run_cli("settings", "--check")
            self.assertEqual(rc, 1, bad)
            self.assertNotIn("Traceback", out + err, bad)
        (self.tmp / "profile" / "settings.json").write_text("{not json")
        rc, _, err = self.run_cli("check", "https://acme.com/careers/1234567", "--company", "Acme")
        self.assertIn("could not be read", err, "the hold window must not fall back silently")

    def test_true_is_not_a_number_of_days(self):
        self.with_template()
        (self.tmp / "profile").mkdir()
        (self.tmp / "profile" / "settings.json").write_text('{"same_company_days": true}')
        rc, out, _ = self.run_cli("settings", "--check")
        self.assertEqual(rc, 1)
        self.assertIn("same_company_days", out)

    def test_check_reads_a_bare_number_as_a_linkedin_id(self):
        # Measured 4 Oct: `check <a bare LinkedIn id>` keyed the number as is and passed a
        # tracked LinkedIn job as new; only `check-many` normalised it.
        self.add(company="Acme", role="Designer",
                 url="https://www.linkedin.com/jobs/view/4468710729/")
        rc, out, _ = self.run_cli("check", "4468710729")
        self.assertEqual(rc, 1)
        self.assertIn("DUPLICATE", out)

    def test_add_refuses_a_duplicate_unless_forced(self):
        url = "https://acme.com/careers/1234567"
        self.add(company="Acme", role="Designer", url=url)
        rc, _, err = self.run_cli("add", "--company", "Acme", "--role", "Designer", "--url", url)
        self.assertEqual(rc, 1)
        self.assertIn("DUPLICATE", err)
        rc, _, _ = self.run_cli("add", "--company", "Acme", "--role", "Designer",
                                "--url", url, "--force")
        self.assertEqual(rc, 0)

    def test_check_many_reads_bare_ids_as_linkedin_and_reports_per_line(self):
        self.add(company="Acme", role="Designer",
                 url="https://www.linkedin.com/jobs/view/4468710729/")
        rc, out, _ = self.run_cli("check-many", stdin="4468710729\n9999999999 | Acme\n\n")
        self.assertEqual(rc, 0)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertTrue(lines[0].startswith("DUP"), lines)
        self.assertTrue(lines[1].startswith("HOLD"), lines)

    # -- status changes ---------------------------------------------------

    def test_a_file_never_changes_folder_when_its_status_changes(self):
        # The whole point of the layout: a rejection a month later edits one line.
        path = self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                        status="applied", date="2026-08-14", applied="2026-08-14")
        self.assertEqual(path.parent.name, "2026-08")
        rc, out, _ = self.run_cli("move", "https://acme.com/careers/1234567", "rejected",
                                  "--note", "form mail, 2 days")
        self.assertEqual(rc, 0)
        self.assertTrue(path.exists(), "the file moved, and it must not")
        text = path.read_text()
        self.assertIn("status: rejected", text)
        self.assertIn("applied: 2026-08-14", text)
        self.assertIn("form mail, 2 days", text)
        self.assertIn("- 2026-08-14: applied", text)  # the original log line survives

    def test_moving_a_skip_row_to_applied_turns_it_into_a_file(self):
        url = "https://acme.com/careers/1234567"
        self.add(company="Acme", role="Designer", url=url, status="skipped",
                 notes="looked like a relocation role")
        rc, out, _ = self.run_cli("move", url, "applied", "--note", "the form had no country gate")
        self.assertEqual(rc, 0)
        path = self.tmp / out.strip().splitlines()[-1]
        self.assertTrue(path.is_file())
        text = path.read_text()
        self.assertIn("status: applied", text)
        self.assertIn(f"applied: {TODAY}", text)
        self.assertIn("Skipped earlier: looked like a relocation role", text)
        self.assertNotIn("1234567", self.skips_table())  # the row is gone

    def test_moving_a_plain_pending_record_to_skipped_turns_it_into_a_row(self):
        url = "https://acme.com/careers/1234567"
        path = self.add(company="Acme", role="Designer", url=url, status="pending")
        rc, _, _ = self.run_cli("move", url, "skipped", "--note", "account wall")
        self.assertEqual(rc, 0)
        self.assertFalse(path.exists())
        self.assertIn("account wall", self.skips_table())

    def test_a_skip_that_was_once_a_real_application_stays_a_file(self):
        # Collapsing it would drop the submitted answers, which is what makes
        # "no sentence goes to two companies" checkable.
        url = "https://acme.com/careers/1234567"
        path = self.add(company="Acme", role="Designer", url=url, status="applied",
                        answers="Why us: because the design system work is real.")
        rc, out, err = self.run_cli("move", url, "skipped", "--note", "withdrew")
        self.assertEqual(rc, 0)
        self.assertTrue(path.exists(), "an application with answers must not become a row")
        self.assertIn("kept as a file", err)
        text = path.read_text()
        self.assertIn("status: skipped", text)
        self.assertIn("design system work is real", text)

    def test_reannotating_a_skip_keeps_the_original_reason(self):
        url = "https://acme.com/careers/1234567"
        self.add(company="Acme", role="Designer", url=url, status="skipped",
                 notes="country list leaves out yours")
        rc, _, _ = self.run_cli("move", url, "skipped", "--note", "the form had no country field")
        self.assertEqual(rc, 0)
        table = self.skips_table()
        self.assertIn("country list leaves out yours", table)
        self.assertIn(f"[{TODAY}] the form had no country field", table)

    def test_running_the_same_move_twice_logs_it_once(self):
        # Measured 6 Oct: a batch of rejections was run twice and 27 records got the
        # same log line twice.
        path = self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567")
        for _ in range(2):
            rc, _, err = self.run_cli("move", "https://acme.com/careers/1234567", "rejected",
                                      "--note", "2026-10-05, not moving forward")
            self.assertEqual(rc, 0, err)
        self.assertEqual(path.read_text().count("not moving forward"), 1)

    def test_normalize_collapses_a_repeated_log_line(self):
        path = self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567")
        line = "- 2026-10-06: rejected. 2026-10-05, not moving forward\n"
        path.write_text(path.read_text().rstrip() + "\n" + line + line)
        rc, out, _ = self.run_cli("normalize")
        self.assertEqual(rc, 0)
        self.assertEqual(path.read_text().count("not moving forward"), 1)

    def test_move_appends_answers_to_an_existing_record(self):
        # Measured 6 Oct: a hand-off's answers arrived after the record existed, and the
        # only way to store them was a hand edit the tracker forbids.
        path = self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                        status="pending")
        rc, _, err = self.run_cli("move", "https://acme.com/careers/1234567", "applied",
                                  "--note", "submitted", "--answers", "Teams: design 4-6")
        self.assertEqual(rc, 0, err)
        text = path.read_text()
        self.assertIn("Teams: design 4-6", text)
        self.assertLess(text.index("Teams: design 4-6"), text.index("## Log"))

    def test_move_rejects_an_unknown_status_and_an_unknown_target(self):
        rc, _, err = self.run_cli("move", "https://acme.com/x", "ghosted")
        self.assertEqual(rc, 1)
        self.assertIn("status must be one of", err)
        rc, _, err = self.run_cli("move", "https://nowhere.example.com/1234567", "rejected")
        self.assertEqual(rc, 1)
        self.assertIn("not found", err)

    # -- generated views and reports --------------------------------------

    def test_stats_counts_sent_and_the_response_rate(self):
        self.add(company="A", role="D", url="https://a.com/careers/1111111", status="applied")
        self.add(company="B", role="D", url="https://b.com/careers/2222222", status="rejected")
        self.add(company="C", role="D", url="https://c.com/careers/3333333", status="pending")
        self.add(company="D", role="D", url="https://d.com/careers/4444444", status="skipped",
                 notes="sector")
        rc, out, _ = self.run_cli("stats")
        self.assertEqual(rc, 0)
        s = json.loads(out)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["sent"], 2)           # applied + rejected; pending and skipped are not sent
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["skipped"], 1)
        self.assertEqual(s["response_rate"], 0.5)

    def test_stats_response_rate_is_none_rather_than_zero_when_nothing_was_sent(self):
        rc, out, _ = self.run_cli("stats")
        self.assertIsNone(json.loads(out)["response_rate"])

    def test_index_writes_the_needs_you_table_and_a_month_page(self):
        self.add(company="Acme", role="Designer", url="https://acme.com/careers/1234567",
                 status="pending", notes="tick the reCAPTCHA yourself")
        rc, _, _ = self.run_cli("index")
        self.assertEqual(rc, 0)
        top = (self.tmp / "applications" / "README.md").read_text()
        self.assertIn("## Needs you (1)", top)
        self.assertIn("tick the reCAPTCHA yourself", top)
        month = (self.tmp / "applications" / TODAY[:7] / "README.md").read_text()
        self.assertIn("Acme", month)

    def test_list_filters_by_status_and_date(self):
        self.add(company="Old", role="D", url="https://old.com/careers/1111111",
                 status="applied", date="2026-01-05", applied="2026-01-05")
        self.add(company="New", role="D", url="https://new.com/careers/2222222", status="applied")
        rc, out, _ = self.run_cli("list", "--since", "2026-06-01")
        self.assertIn("New", out)
        self.assertNotIn("Old", out)
        rc, out, _ = self.run_cli("list", "--status", "pending")
        self.assertEqual(out.strip(), "")

    # -- normalize --------------------------------------------------------

    def test_normalize_dry_run_changes_nothing_on_disk(self):
        path = self.add(company="Acme", role="Designer",
                        url="https://boards.greenhouse.io/acme/jobs/4567890")
        path.write_text(path.read_text().replace("source: ", "source: Greenhouse"))
        before = path.read_text()
        rc, out, _ = self.run_cli("normalize", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("would change", out)
        self.assertEqual(path.read_text(), before)
        rc, out, _ = self.run_cli("normalize")
        self.assertIn("normalised", out)
        self.assertIn("ats: greenhouse", path.read_text())

    def test_normalize_is_stable_on_a_second_pass(self):
        # `normalize` counting work forever would mean it is rewriting files on
        # every run, which is how a hand-editable tracker drifts.
        self.add(company="Acme", role="Designer", url="https://jobs.lever.co/acme/1234567")
        self.add(company="Bcme", role="Designer", url="https://b.com/careers/7654321",
                 status="skipped", notes="pipe | in the reason")
        self.run_cli("normalize")
        rc, out, _ = self.run_cli("normalize")
        self.assertEqual(out.strip(), "0 files and 0 skip rows normalised")

    def test_a_skip_reason_containing_a_pipe_does_not_grow_backslashes(self):
        self.add(company="Acme", role="Designer", url="https://a.com/careers/1111111",
                 status="skipped", notes="country list: DE | IT | PT")
        # A second skip in the same month rewrites the whole table.
        self.add(company="Bcme", role="Designer", url="https://b.com/careers/2222222",
                 status="skipped", notes="sector")
        self.run_cli("normalize")
        table = self.skips_table()
        self.assertIn(r"country list: DE \| IT \| PT", table)
        self.assertNotIn(r"\\|", table)


class FreehireSweepTests(unittest.TestCase):
    """The sweep script against a throwaway repository and a fake curl that logs
    each request and answers with no jobs, so nothing touches the network."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="seekter-sweep-"))
        (self.tmp / "scripts").mkdir()
        for name in ("seekter.py", "freehire_sweep.py"):
            shutil.copy2(SCRIPT.parent / name, self.tmp / "scripts" / name)
        (self.tmp / "templates").mkdir()
        shutil.copy2(REPO / "templates" / "settings.json", self.tmp / "templates" / "settings.json")
        (self.tmp / "applications").mkdir()
        (self.tmp / "profile").mkdir()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        self.log = self.tmp / "curl.log"
        fake = bin_dir / "curl"
        fake.write_text(f'#!/bin/sh\nfor a in "$@"; do last="$a"; done\necho "$last" >> "{self.log}"\n'
                        'echo \'{"data": []}\'\n')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sweep(self, freehire):
        settings = {"title_keep": "designer", "freehire": dict(queries=["product designer"],
                                                               categories=["design"], **freehire)}
        (self.tmp / "profile" / "settings.json").write_text(json.dumps(settings))
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py")],
                           capture_output=True, text=True, env=self.env)
        urls = self.log.read_text().splitlines() if self.log.exists() else []
        return p.returncode, p.stderr, urls

    def test_failed_requests_report_incomplete_and_all_fail_exit_three(self):
        fake = self.tmp / "bin" / "curl"
        for partial in (False, True):
            fake.write_text('#!/bin/sh\nfor a in "$@"; do last="$a"; done\n' +
                            ('case "$last" in *regions=eu*) echo \'{"data": []}\'; exit 0;; esac\n' if partial else '') + 'exit 7\n')
            self.sweep(dict(regions=["eu", "other"], home_country=""))
            proc = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py")],
                                  capture_output=True, text=True, env=self.env)
            self.assertEqual(proc.returncode, 0 if partial else 3, proc.stderr)
            self.assertIn(f"problem: {1 if partial else 2} of 2 freehire requests failed", proc.stdout)
            self.assertEqual(json.loads((self.tmp / "runs" / TODAY / "freehire.json").read_text()), [])

    def test_bad_json_is_a_failed_request(self):
        (self.tmp / "bin" / "curl").write_text('#!/bin/sh\necho broken-json\n')
        self.sweep(dict(regions=["eu"], home_country=""))
        proc = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py")],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("problem: 1 of 1 freehire requests failed", proc.stdout)

    def test_detail_quotes_slug_and_prints_in_order(self):
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        (run / "freehire.json").write_text(json.dumps([
            dict(slug="slow/one?x", company="Acme", title="Designer", url="https://example.com/one"),
            dict(slug="fast", company="Acme", title="Designer", url="https://example.com/two")]))
        fake = self.tmp / "bin" / "curl"
        fake.write_text(f'#!{sys.executable}\nimport json, sys, time\nu=sys.argv[-1]\nwith open({str(self.log)!r}, "a") as f: f.write(u+"\\n")\nif "slow" in u: time.sleep(0.15)\nprint(json.dumps(dict(data=dict(description="Remote role"))))\n')
        proc = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py"), "--detail", "0, 1"],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(proc.stdout.index("===== 0"), proc.stdout.index("===== 1"))
        self.assertIn("https://freehire.me/api/v1/jobs/slow%2Fone%3Fx", self.log.read_text())

    def test_parsed_search_error_counts_as_one_failure_per_request(self):
        fake = self.tmp / "bin" / "curl"
        for response in ('{"error":"x"}', '{"data":{}}', '[]'):
            fake.write_text(f"#!/bin/sh\necho '{response}'\n")
            self.sweep(dict(regions=["eu"], home_country=""))
            p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py")], capture_output=True, text=True, env=self.env)
            self.assertEqual(p.returncode, 3, p.stderr)
            self.assertIn("problem: 1 of 1 freehire requests failed", p.stdout)

    def test_detail_validates_indices_and_missing_output(self):
        script = self.tmp / "scripts" / "freehire_sweep.py"
        proc = subprocess.run([sys.executable, str(script), "--detail", "99"], capture_output=True, text=True, env=self.env)
        self.assertIn("problem: run the sweep first", proc.stderr)
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        (run / "freehire.json").write_text('[{"slug": "acme"}]')
        for index in ("99", "x", "-1", "²"):
            proc = subprocess.run([sys.executable, str(script), "--detail", index], capture_output=True, text=True, env=self.env)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("problem: no candidate " + index, proc.stderr)
        self.assertFalse(self.log.exists())

    def configure_screening(self, **extra):
        cfg = dict(title_keep="designer", title_drop="interior", blocked_companies=["Blocked"],
                   location_flags=["US only"], languages=["en"], blocked_domains=["adult"],
                   freehire=dict(queries=["designer"], categories=["design"], regions=["eu"], home_country="", window_days=3))
        cfg.update(extra)
        (self.tmp / "profile" / "settings.json").write_text(json.dumps(cfg))

    def fake_jobs(self, rows):
        board = self.tmp / "jobs.json"
        board.write_text(json.dumps(dict(data=rows)))
        (self.tmp / "bin" / "curl").write_text(f'#!/bin/sh\ncat "{board}"\n')

    def test_sweep_records_every_drop_stage_and_keeps_flag(self):
        self.configure_screening()
        subprocess.run([sys.executable, str(self.tmp / "scripts" / "seekter.py"), "add", "--company", "Acme", "--role", "Designer", "--url", "https://example.com/tracked"], check=True, capture_output=True)
        def job(slug, **values):
            row = dict(slug=slug, title="Designer", company="Acme", location="Remote", url="https://example.com/" + slug, posted_at=TODAY + "T00:00:00Z")
            row.update(values)
            return row
        self.fake_jobs([job("closed", closed_at=TODAY), job("sector", enrichment=dict(domains=["adult"])),
                        job("language", enrichment=dict(posting_language="zz")), job("drop", title="Interior Designer"),
                        job("keep", title="Engineer"), job("company", company="Blocked Ltd"),
                        job("stale", posted_at="2000-01-01T00:00:00Z"), job("tracked"),
                        job("flag", countries=["US only"]), job("consider", title="Designer\x1b[2J")])
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py")], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        run = self.tmp / "runs" / TODAY
        rows = json.loads((run / "freehire.json").read_text())
        self.assertEqual([r["slug"] for r in rows], ["flag", "consider"])
        self.assertEqual(rows[0]["flag"], "location: US only")
        dropped = json.loads((run / "freehire-dropped.json").read_text())
        reasons = {r["url"].rsplit("/", 1)[-1]: r["reason"] for r in dropped}
        self.assertEqual(len(reasons), 8)
        self.assertEqual(reasons["drop"], "title_drop: Interior")
        self.assertEqual(reasons["keep"], "title_keep: no match")
        self.assertEqual(reasons["company"], "blocked company: Blocked")
        for stage in ("closed", "sector", "language", "stale", "tracked"):
            self.assertTrue(reasons[stage].startswith(stage))
        self.assertIn(f"dropped postings and their reasons: runs/{TODAY}/freehire-dropped.json", p.stdout)
        self.assertIn('"company": 1', p.stdout)

    def test_freehire_validates_phrase_settings_before_network(self):
        self.configure_screening(location_flags="US only")
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py")], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("problem: profile/settings.json location_flags must be a list of names/phrases", p.stderr)
        self.assertFalse(self.log.exists())

    def test_sample_needs_no_title_keep_and_keeps_excluded_titles(self):
        self.configure_screening(title_keep="")
        self.fake_jobs([dict(slug="1", title="Designer"), dict(slug="2", title="designer"),
                        dict(slug="3", title="Interior Designer"), dict(slug="4", title="Closed", closed_at=TODAY)])
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py"), "--sample"], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((self.tmp / "runs" / TODAY / "sample-titles.txt").read_text().splitlines(), ["Designer", "Interior Designer"])
        self.assertIn("2 real titles →", p.stdout)

    def test_detail_reports_the_failed_entry(self):
        run = self.tmp / "runs" / TODAY
        run.mkdir(parents=True)
        (run / "freehire.json").write_text(json.dumps([dict(slug=slug, company="Acme", title="Designer", url="https://example.com/" + slug) for slug in ("fail", "ok")]))
        (self.tmp / "bin" / "curl").write_text('#!/bin/sh\nfor a in "$@"; do last="$a"; done\ncase "$last" in */fail) exit 7;; esac\necho \'{"data": {"description": "Remote role"}}\'\n')
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "freehire_sweep.py"), "--detail", "all"], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.count("(fetch failed)"), 1)
        self.assertIn("problem: 1 of 2 detail requests failed; those entries show no eligibility lines", p.stdout)
        self.assertIn("  - Remote role", p.stdout)

    def test_a_home_country_without_regions_sweeps_by_country_only(self):
        # PR #39: freehire's own region for Turkey returns 0, so a home-country-only
        # candidate sets regions to [] and must still get the countries pass.
        rc, err, urls = self.sweep({"regions": [], "home_country": "tr"})
        self.assertEqual(rc, 0, err)
        self.assertTrue(urls)
        self.assertTrue(all("countries=tr" in u for u in urls), urls)
        self.assertFalse(any("regions=" in u for u in urls), urls)

    def test_neither_regions_nor_a_home_country_stops_the_sweep(self):
        rc, err, urls = self.sweep({"regions": [], "home_country": ""})
        self.assertNotEqual(rc, 0)
        self.assertIn("No freehire regions", err)
        self.assertEqual(urls, [])



class EmployerSweepTests(unittest.TestCase):
    """The watchlist sweep against a throwaway repository and a fake curl that
    answers one Greenhouse board and nothing else."""

    BOARD = {"jobs": [
        {"id": 1, "title": "Senior Product Designer", "location": {"name": "Remote, Europe"}},
        {"id": 2, "title": "Interior Designer", "location": {"name": "Berlin"}},
        {"id": 3, "title": "Backend Engineer", "location": {"name": "Remote"}},
        {"id": 4, "title": "Product Designer, Growth", "location": {"name": "Remote"}},
    ]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="seekter-employers-"))
        (self.tmp / "scripts").mkdir()
        for name in ("seekter.py", "employer_sweep.py"):
            shutil.copy2(SCRIPT.parent / name, self.tmp / "scripts" / name)
        (self.tmp / "templates").mkdir()
        shutil.copy2(REPO / "templates" / "settings.json", self.tmp / "templates" / "settings.json")
        (self.tmp / "applications").mkdir()
        (self.tmp / "profile").mkdir()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        board = self.tmp / "board.json"
        board.write_text(json.dumps(self.BOARD))
        fake = bin_dir / "curl"
        fake.write_text('#!/bin/sh\nfor a in "$@"; do last="$a"; done\n'
                        f'case "$last" in *boards-api.greenhouse.io/v1/boards/acme/*) cat "{board}";; '
                        '*) echo "{}";; esac\n')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sweep(self, employers):
        settings = {"title_keep": "designer", "title_drop": "interior", "employers": employers}
        (self.tmp / "profile" / "settings.json").write_text(json.dumps(settings))
        return subprocess.run([sys.executable, str(self.tmp / "scripts" / "employer_sweep.py")],
                              capture_output=True, text=True, env=self.env)

    def test_hn_keeps_body_matches_and_drops_header_only_exclusions(self):
        # HN headers often name the company and location before any role appears.
        body = " details" * 30 + " hiring a Designer"
        responses = self.tmp / "hn-responses.json"
        responses.write_text(json.dumps({
            "search": dict(hits=[dict(title="Ask HN: Who is hiring?", objectID="100")]),
            "thread": dict(children=[
                dict(id=101, text="Acme | Remote" + body),
                dict(id=102, text="Acme | Interior | Remote" + body),
                dict(id=103, text="Acme | Interior Designer | Remote"),
                dict(id=104, text="Acme | Remote | Engineer")])}))
        fake = self.tmp / "bin" / "curl"
        fake.write_text(f'#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\nd=json.loads(Path({str(responses)!r}).read_text())\nprint(json.dumps(d["search" if "search_by_date" in sys.argv[-1] else "thread"]))\n')
        (self.tmp / "profile" / "settings.json").write_text(json.dumps(dict(title_keep="designer", title_drop="interior")))
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "employer_sweep.py"), "--hn"],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = json.loads((self.tmp / "runs" / TODAY / "hn.json").read_text())
        self.assertEqual([row["id"] for row in rows], [101, 103])
        self.assertNotIn("Designer", rows[0]["header"])

    def test_sweep_rejects_string_phrase_settings(self):
        log = self.tmp / "early-validation.log"
        (self.tmp / "bin" / "curl").write_text(f'#!/bin/sh\necho called >> "{log}"\necho "{{}}"\n')
        for key in ("blocked_companies", "location_flags"):
            (self.tmp / "profile" / "settings.json").write_text(json.dumps(dict(title_keep="designer", employers=[dict(name="Acme", ats="greenhouse", slug="acme")], **{key: "US only"})))
            for args in ([], ["--hn"]):
                p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "employer_sweep.py"), *args], capture_output=True, text=True, env=self.env)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(f"problem: profile/settings.json {key} must be a list of names/phrases", p.stderr)
                self.assertFalse(log.exists())

    def test_employers_screen_companies_and_flag_locations(self):
        (self.tmp / "profile" / "settings.json").write_text(json.dumps(dict(title_keep="designer", title_drop="interior", blocked_companies=["Blocked"], location_flags=["Europe"], employers=[dict(name=name, ats="greenhouse", slug="acme") for name in ("Blocked", "Acme")])))
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "employer_sweep.py")], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        run = self.tmp / "runs" / TODAY
        rows = json.loads((run / "employers.json").read_text())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["flag"], "location: Europe")
        dropped = json.loads((run / "employers-dropped.json").read_text())
        self.assertEqual(sum(r["reason"] == "blocked company: Blocked" for r in dropped), 4)
        self.assertIn("title_drop: Interior", [r["reason"] for r in dropped])
        self.assertIn("title_keep: no match", [r["reason"] for r in dropped])

    def test_hn_screening_keeps_body_rule_and_logs_drops(self):
        responses = self.tmp / "screened-hn.json"
        responses.write_text(json.dumps({
            "search": dict(hits=[dict(title="Ask HN: Who is hiring?", objectID="100")]),
            "thread": dict(children=[
                dict(id=101, text="Blocked | Designer | Remote"),
                dict(id=102, text="Acme | US only" + " details" * 30 + " Designer"),
                dict(id=103, text="Acme | Interior | Remote" + " details" * 30 + " Designer"),
                dict(id=104, text="Acme | Engineer"),
                dict(id=105, text="Blocked Designer")])}))
        (self.tmp / "bin" / "curl").write_text(f'#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\nd=json.loads(Path({str(responses)!r}).read_text())\nprint(json.dumps(d["search" if "search_by_date" in sys.argv[-1] else "thread"]))\n')
        (self.tmp / "profile" / "settings.json").write_text(json.dumps(dict(title_keep="designer", title_drop="interior", blocked_companies=["Blocked"], location_flags=["US only"])))
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "employer_sweep.py"), "--hn"], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        run = self.tmp / "runs" / TODAY
        rows = json.loads((run / "hn.json").read_text())
        self.assertEqual([r["id"] for r in rows], [102, 105])
        self.assertEqual(rows[0]["flag"], "location: US only")
        drops = json.loads((run / "hn-dropped.json").read_text())
        self.assertEqual([r["reason"] for r in drops], ["blocked company: Blocked", "title_drop: Interior", "title_keep: no match"])

    def test_new_boards_dates_and_invalid_slug(self):
        boards = self.tmp / "boards.json"
        boards.write_text(json.dumps({
            "smartrecruiters": {"content": [dict(id="123", name="Designer", location=dict(city="City", country="Country", remote=True), releasedDate=TODAY + "T00:00:00Z"), dict(id="456", name="Interior Designer")]},
            "recruitee": {"offers": [dict(title="Designer", location="City", remote=True, careers_url="https://acme.recruitee.com/o/designer", published_at=TODAY)]},
            "greenhouse": {"jobs": [dict(id=1, title="Designer", updated_at=TODAY + "T12:00:00Z")]}}))
        log = self.tmp / "requests.jsonl"
        fake = self.tmp / "bin" / "curl"
        fake.write_text(f'#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\nu=sys.argv[-1]\nwith open({str(log)!r}, "a") as f: f.write(u+"\\n")\nd=json.loads(Path({str(boards)!r}).read_text())\nprint(json.dumps(d["smartrecruiters" if "smartrecruiters" in u else "recruitee" if "recruitee" in u else "greenhouse"]))\n')
        p = self.sweep([dict(name="Acme", ats=ats, slug="acme") for ats in ("smartrecruiters", "recruitee", "greenhouse")] + [dict(name="Bad", ats="greenhouse", slug="../x")])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('problem: Bad: slug "../x" has characters a board id never has', p.stdout)
        requests = log.read_text().splitlines()
        self.assertEqual(len(requests), 3)
        self.assertTrue(all("../x" not in x for x in requests))
        rows = json.loads((self.tmp / "runs" / TODAY / "employers.json").read_text())
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["url"], "https://jobs.smartrecruiters.com/acme/123")
        self.assertEqual(rows[0]["location"], "City Country remote")
        self.assertEqual(rows[1]["url"], "https://acme.recruitee.com/o/designer")
        self.assertTrue(all(row["posted"] == TODAY for row in rows))

    def test_invalid_title_regex_exits_before_request(self):
        (self.tmp / "profile" / "settings.json").write_text(json.dumps(dict(title_keep="[", employers=[dict(name="Acme", ats="greenhouse", slug="acme")])))
        p = subprocess.run([sys.executable, str(self.tmp / "scripts" / "employer_sweep.py")], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not a valid regex", p.stderr)

    def test_titles_are_filtered_and_tracked_postings_dropped(self):
        subprocess.run([sys.executable, str(self.tmp / "scripts" / "seekter.py"), "add", "--company", "Acme",
                        "--role", "Product Designer, Growth", "--status", "applied",
                        "--url", "https://job-boards.greenhouse.io/acme/jobs/4"],
                       capture_output=True, text=True, check=True)
        p = self.sweep([{"name": "Acme", "ats": "greenhouse", "slug": "acme"}])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("1 boards, 4 postings → 1 candidates", p.stdout)
        self.assertIn("Senior Product Designer", p.stdout)
        self.assertNotIn("Interior", p.stdout)
        self.assertNotIn("Growth", p.stdout)

    def test_a_wrong_slug_is_reported_not_guessed(self):
        p = self.sweep([{"name": "Nobody", "ats": "ashby", "slug": "nobody"},
                        {"name": "Odd", "ats": "taleo", "slug": "odd"}])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("problem: Nobody: no board at ashby:nobody", p.stdout)
        self.assertIn("problem: Odd: no board at taleo:odd", p.stdout)

    def test_missing_and_empty_slugs_are_reported_without_requests(self):
        fake = self.tmp / "bin" / "curl"
        log = self.tmp / "slug-requests.log"
        fake.write_text(f'#!/bin/sh\necho called >> "{log}"\necho "{{}}"\n')
        p = self.sweep([dict(name="Acme", ats="greenhouse"),
                        dict(name="Other", ats="greenhouse", slug="")])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("problem: Acme: no slug", p.stdout)
        self.assertIn("problem: Other: no slug", p.stdout)
        self.assertNotIn("has characters", p.stdout)
        self.assertFalse(log.exists())

    def test_an_empty_watchlist_stops_the_sweep(self):
        p = self.sweep([])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("No employers", p.stderr)

if __name__ == "__main__":
    unittest.main(verbosity=2)
