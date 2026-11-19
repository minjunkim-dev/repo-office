"""The office pipeline: stage order, sessions, permissions, approvals and stops, with a fake worker."""

import contextlib
import errno
import fcntl
import importlib.util
import io
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock


OFFICE = Path(__file__).resolve().parent


def load_engine(jobs):
    os.environ["OFFICE_JOBS"] = str(jobs)
    os.environ["OFFICE_RETRY_WAIT"] = "0"  # the engine's one retry of a transient error, without the wait
    os.environ["OFFICE_CI_EVERY"], os.environ["OFFICE_CI_TIMEOUT"] = "0.01", "5"  # the ci stage asks the fake gh at once
    spec = importlib.util.spec_from_file_location("engine", OFFICE / "pipeline/engine.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    return engine


class FakeWorker:
    """Records each launch and writes the stage's result file, like a worker that finished its turn."""

    def __init__(self, job, skip=()):
        self.job, self.skip, self.calls = job, list(skip), []

    def __call__(self, name, cwd, argv, message):
        stage = name.rsplit("-", 1)[1]
        self.calls.append((stage, argv, message))
        if stage in self.skip:
            self.skip.remove(stage)
            return None
        (self.job / f"{stage}.md").write_text(f"{stage} {len(self.calls)}\n", encoding="utf-8")
        if stage == "implement":
            subprocess.run(["git", "-C", str(self.job / "repo"), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            (self.job / "pr-body.md").write_text("# fix: x\n\n## Summary\n", encoding="utf-8")
        if stage == "review":
            (self.job / "review-body.md").write_text("## Summary\n\n## Review\nok\n", encoding="utf-8")
            head = subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            (self.job / "review.md").write_text(f"리뷰한 head와 Door: {head}, 되돌리기 어려움\n고친 것: P0 0, P1 1, P2 0. x\n"
                                                "남은 지적: [Spec] P1 y, a.py:3\n", encoding="utf-8")
        return None


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = load_engine(Path(self.tmp.name))
        self.job = Path(self.tmp.name) / "search"
        repo = self.job / "repo"
        repo.mkdir(parents=True)
        for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                     ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        self.test_cmd = "test -f pass"
        (self.job / "task.json").write_text(json.dumps(
            {"name": "search", "repo": str(repo), "test": self.test_cmd, "base": "main"}))
        (self.job / "INTENT.md").write_text("# search\n", encoding="utf-8")
        (repo / ".git/info/exclude").write_text("pass\n", encoding="utf-8")
        (repo / "pass").write_text("", encoding="utf-8")
        self.baseline = Path(self.tmp.name) / "baseline"  # a repo-baseline clone outside the office checkout
        (self.baseline / "tools").mkdir(parents=True)
        self.engine.BASELINE = self.baseline
        # The engine now runs gh writes itself: no test may reach the real gh. fake_gh() puts a recording one first.
        stub = Path(self.tmp.name) / "nogh"
        stub.mkdir()
        (stub / "gh").write_text("#!/bin/sh\necho 'no gh in tests' >&2\nexit 1\n", encoding="utf-8")
        (stub / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self):
        return json.loads((self.job / "state.json").read_text())

    def runs(self):
        """state.json models without their start and end times."""
        return [{k: v for k, v in m.items() if k not in ("start", "end")} for m in self.state()["models"]]

    def fake_github(self):
        """origin is a GitHub URL whose pushes land in a local bare repository; gh records its calls."""
        bare, bin_dir = Path(self.tmp.name) / "origin.git", Path(self.tmp.name) / "ghbin"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        repo = ["git", "-C", str(self.job / "repo")]
        subprocess.run([*repo, "remote", "add", "origin", "https://github.com/o/r.git"], check=True)
        subprocess.run([*repo, "config", f"url.{bare}.insteadOf", "https://github.com/o/r.git"], check=True)
        subprocess.run([*repo, "push", "-q", "origin", "HEAD:refs/heads/main"], check=True)  # the PR base
        return (bare, *self.fake_gh())

    def fake_gh(self):
        """gh records its calls; `pr edit` also records what it read on stdin as a last item."""
        bin_dir = Path(self.tmp.name) / "ghbin"
        bin_dir.mkdir()
        log, draft = Path(self.tmp.name) / "gh.log", Path(self.tmp.name) / "draft"
        draft.write_text("true\n", encoding="utf-8")
        # The ci stage's question: the PR head is the task's HEAD, and its checks are self.checks (green by default).
        self.checks = Path(self.tmp.name) / "checks.json"
        self.checks.write_text('[{"name": "CI", "status": "COMPLETED", "conclusion": "SUCCESS"}]', encoding="utf-8")
        # The ci stage's look before it queues an easy PR: the PR's node id, base and branch.
        self.pr_json = Path(self.tmp.name) / "pr.json"
        self.pr_json.write_text('{"id": "PR_7", "baseRefName": "main", "headRefName": "office/search"}', encoding="utf-8")
        self.api_notes = Path(self.tmp.name) / "notes"  # what each REST list of reviews and comments prints: [id, body] per line
        self.api_notes.write_text("", encoding="utf-8")
        (bin_dir / "gh").write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            "stdin = [sys.stdin.read()] if sys.argv[1:3] == ['pr', 'edit'] else []\n"
            f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:] + stdin) + '\\n')\n"
            "if sys.argv[1:3] == ['pr', 'create']:\n"
            f"    open({str(draft)!r}, 'w').write(str('--draft' in sys.argv).lower())\n"
            "    print('https://github.com/O/R/pull/7')\n"
            "if sys.argv[1:3] == ['pr', 'view'] and 'headRefOid,statusCheckRollup' in sys.argv:\n"
            "    import subprocess\n"
            f"    head = subprocess.run(['git', '-C', {str(self.job / 'repo')!r}, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()\n"
            f"    print(json.dumps({{'headRefOid': head, 'statusCheckRollup': json.load(open({str(self.checks)!r}))}}))\n"
            f"elif sys.argv[1:3] == ['pr', 'view'] and 'id,baseRefName,headRefName' in sys.argv: print(open({str(self.pr_json)!r}).read())\n"
            f"elif sys.argv[1:3] == ['pr', 'view']: print(open({str(draft)!r}).read().strip())\n"
            f"if sys.argv[1:3] == ['pr', 'ready']: open({str(draft)!r}, 'w').write('false')\n"
            f"if sys.argv[1:2] == ['api'] and any(a.endswith('/comments') for a in sys.argv): print(open({str(self.api_notes)!r}).read(), end='')\n",
            encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        calls = lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []  # noqa: E731
        return draft, calls

    def test_full_flow_waits_only_for_the_design_approval_and_keeps_contexts_apart(self):
        bare, draft, gh = self.fake_github()
        worker = FakeWorker(self.job)
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertEqual(self.state()["stage"], "approve")
        self.assertEqual(self.engine.approve("search"), 0)
        # An office checkout whose origin differs from the PR's repository, whatever this checkout's origin is.
        office = Path(self.tmp.name) / "office"
        subprocess.run(["git", "init", "-q", str(office)], check=True)
        subprocess.run(["git", "-C", str(office), "remote", "add", "origin", "https://github.com/me/office.git"], check=True)
        self.engine.BASELINE = office
        # From the approval to the end with no person: the engine marks the PR ready and asks the review bot once.
        self.assertEqual(self.engine.run("search", worker), self.engine.DONE)
        self.assertIn("exit 0", (self.job / "check.md").read_text())
        head = subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        self.assertEqual(subprocess.run(["git", "-C", str(bare), "rev-parse", "office/search"], capture_output=True, text=True).stdout, head)
        create = gh()[0]
        self.assertEqual(create[:3], ["pr", "create", "--repo"])
        self.assertEqual([create[create.index(f) + 1] for f in ("--repo", "--head", "--base", "--title", "--body")],
                         ["o/r", "office/search", "main", "fix: x", "## Summary\n"])
        self.assertNotIn("--draft", create)  # a draft marked ready at once ran CI twice
        self.assertEqual((self.job / "pr.txt").read_text(), "https://github.com/O/R/pull/7\n")
        self.assertNotIn("branch.office/search.remote", subprocess.run(
            ["git", "-C", str(self.job / "repo"), "config", "--list"], capture_output=True, text=True).stdout)  # no -u
        writes = [c[:2] for c in gh() if not any("mergeStateStatus" in a for a in c)]  # the board's merge probe only reads
        self.assertEqual(writes, [["pr", "create"], ["pr", "view"], ["pr", "comment"], ["pr", "edit"], ["pr", "view"]])  # the last asks for CI
        self.assertIn("@coderabbitai review", next(c for c in gh() if c[:2] == ["pr", "comment"]))
        report = (self.job / "REPORT.md").read_text()
        head = subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        for part in ("https://github.com/O/R/pull/7", f"gh pr view https://github.com/O/R/pull/7 --json id --jq .id)\" -f sha={head}`",
                     # The queue refuses another head only when the mutation passes the reviewed SHA.
                     "enqueuePullRequest(input:{pullRequestId:$id,expectedHeadOid:$sha})", "- design: opus",
                     "- implement: sonnet", "- review: opus", "사람이 diff를 읽고 병합한다", "exit 0",
                     "리뷰가 고친 지적: P0 0, P1 1, P2 0", "남은 지적: [Spec] P1 y, a.py:3",
                     "PR 브랜치에 base를 합치지 않는다"):
            self.assertIn(part, report)
        self.assertNotIn("gh pr merge", report)  # it arms auto-merge on a merge queue branch
        # Without the reviewed head in review.md there is no merge command: never a SHA read from repo/.
        (self.job / "review.md").write_text("리뷰한 head와 Door: abc1234, 되돌리기 어려움\n- 확인하지 않은 범위: z\n", encoding="utf-8")
        self.engine.write_report(self.job, json.loads((self.job / "task.json").read_text()), self.state(), [])
        report = (self.job / "REPORT.md").read_text()
        self.assertNotIn("enqueuePullRequest", report)
        self.assertIn("\n- 확인하지 않은 범위: z\n", report)  # a bullet line keeps one bullet
        self.engine.save(self.job / "state.json", {**self.state(), "stage": "ready"})  # the ready stage runs again
        self.assertEqual(self.engine.run("search", worker), self.engine.DONE)
        self.assertEqual([c[:2] for c in gh()].count(["pr", "comment"]), 1)  # asked once
        # The engine, not the reviewer, posts the review's body: as bytes on stdin to the pr.txt URL.
        self.assertEqual(next(c for c in gh() if c[:2] == ["pr", "edit"]), ["pr", "edit", "https://github.com/O/R/pull/7", "--body-file", "-",
                                    "## Summary\n\n## Review\nok\n"])

        (design, d_argv, message), (implement, i_argv, _), (review, r_argv, _) = worker.calls[:3]
        self.assertEqual((design, implement, review), ("design", "implement", "review"))
        work = self.engine.session_id("search", "work")
        self.assertEqual(d_argv[d_argv.index("--session-id") + 1], work)
        self.assertEqual(i_argv[i_argv.index("--resume") + 1], work)
        # The reviewer starts a session of its own: an inherited conversation is not another context.
        self.assertNotIn(work, r_argv)
        self.assertIn("--session-id", r_argv)
        self.assertIn("design.prompt.md", message)
        config = json.loads((OFFICE / "pipeline/stages.json").read_text())
        for argv in (d_argv, i_argv, r_argv):
            # Normal development tools in dontAsk (a prompt would hang the hc worker); deny rules hold in every mode.
            self.assertEqual(argv[:5], ["claude", "--setting-sources", "project", "--permission-mode", "dontAsk"])
            allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
            self.assertEqual(allowed, config["allow"])
            self.assertTrue({"Bash", "Read", "Edit", "Write"} <= set(allowed))
            denied = argv[argv.index("--disallowedTools") + 1:]
            for rule in ("Bash(git push*)", "Bash(git * push*)", "Bash(gh pr merge*)", "Bash(gh pr edit*)", "Bash(gh api *-X*)",
                         "Bash(hc *)"):  # a worker never needs hc: it must not type into the leader
                self.assertIn(rule, denied)
            self.assertNotIn("--settings", argv)  # no sandbox, no read block
        self.assertIn(f"{office}/prompts/review.md", (self.job / "review.prompt.md").read_text())
    def test_a_changed_design_needs_a_new_approval(self):
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        (self.job / "design.md").write_text("changed after approval\n")
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertEqual(self.state()["stage"], "approve")
        self.assertEqual([c[0] for c in worker.calls], ["design"])

    def test_an_easy_design_is_approved_by_the_engine_and_a_hard_one_waits(self):
        design = ["되돌리기: 쉬움\n"]

        class Worker(FakeWorker):
            def __call__(w, name, cwd, argv, message):
                super().__call__(name, cwd, argv, message)
                if name.endswith("-design"):
                    (self.job / "design.md").write_text(design[0], encoding="utf-8")

        worker = Worker(self.job, skip=["implement", "implement"])  # stop after the approval: two tries, then a block
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", worker)
        state = self.state()
        self.assertEqual(state["approved"]["approve"], self.engine.sha(self.job / "design.md"))
        self.assertEqual(state["auto_approved"], state["approved"])  # the board and the report can tell it from a person's
        # A design rewritten as hard goes back to the approval and waits for a person, who then owns the approval.
        for line in ("되돌리기: 어려움\n", "되돌리기: 쉬움\n되돌리기: 어려움\n", "되돌리기 쉬움\n", "난이도: 쉬움\n",
                     "Door: 되돌리기 쉬움\n"):  # the old wording reads as easy for a PR, but a person approves the design
            with self.subTest(line=line):
                (self.job / "BLOCKED.md").unlink(missing_ok=True)
                (self.job / "design.md").write_text(line, encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
                self.assertEqual(self.state()["stage"], "approve")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.approve("search"), 0)
        self.assertNotIn("approve", self.state()["auto_approved"])

    def queued(self, review, notes='[1, "LGTM"]\n', late="", review_files=(), checks=None, base="main", branch="office/search",
               office=None, judged=True, redesign=False, url=None, push="https://github.com/o/r.git", **state):
        """At the post stage with review.md, the PR's comments and state set, run to the end; the enqueue calls and REPORT.md 「병합」.
        notes: the comment lists when the review worker recorded seen.json (judged=False: it never did); late: added afterwards.
        review_files: the review worker commits these after the checked commit the draft stage proved easy."""
        self.at_post()
        if url:
            (self.job / "pr.txt").write_text(url + "\n", encoding="utf-8")
        self.pr_json.write_text(f'{{"id": "PR_7", "baseRefName": "{base}", "headRefName": "{branch}"}}', encoding="utf-8")
        self.api_notes.write_text(notes, encoding="utf-8")
        if judged:
            self.assertEqual(self.engine.record_seen("search"), 0)
        self.api_notes.write_text(notes + late, encoding="utf-8")
        if redesign:  # the design changes while the engine reads the PR, after every earlier approval check
            real = self.engine.pr_notes
            def notes_then_edit(*a):
                with open(self.job / "design.md", "a", encoding="utf-8") as design:
                    design.write("\n한 줄 더\n")
                return real(*a)
            patched = unittest.mock.patch.object(self.engine, "pr_notes", notes_then_edit)
            patched.start()
            self.addCleanup(patched.stop)
        own = "https://github.com/office/office.git" if office is None else office  # the office checkout's origin; "" for none
        # The task repository fetches from a local bare repository (insteadOf): git reports the GitHub URL it stands for
        fetches = {("remote", "get-url", "--all", "origin"): "https://github.com/o/r.git",
                   ("remote", "get-url", "--push", "--all", "origin"): push}
        patch = unittest.mock.patch.object(self.engine.subprocess, "run", side_effect=lambda argv, *a, _run=subprocess.run, **k: (
            subprocess.CompletedProcess(argv, 0, own + "\n", "") if argv[:2] == ["git", "-C"] and argv[2] == str(self.engine.OFFICE)
            and argv[3:] in (["config", "--get", "remote.origin.url"], ["remote", "get-url", "origin"])
            else subprocess.CompletedProcess(argv, 0, fetches[tuple(argv[3:])] + "\n", "")
            if argv[:3] == ["git", "-C", str(self.job / "repo")] and tuple(argv[3:]) in fetches else _run(argv, *a, **k)))
        patch.start()
        self.addCleanup(patch.stop)
        if checks is not None:
            self.checks.write_text(checks, encoding="utf-8")
            self.engine.CI_NONE = 0
            self.addCleanup(setattr, self.engine, "CI_NONE", 300)
        checked = subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        head = self.review_commit(*review_files) if review_files else checked
        state = {"easy_pr": checked, "checked": checked, **state}
        (self.job / "review.md").write_text(review.format(head=head), encoding="utf-8")
        self.engine.save(self.job / "state.json", {**self.state(), "models": [
            {"stage": "review", "model": "sonnet", "effort": "medium", "rerun": False, "start": 0, "end": int(time.time()) - 60}], **state})
        self.assertEqual(self.post(), self.engine.DONE)
        calls = [c for c in self.gh_calls() if c[:2] == ["api", "graphql"] and any("enqueuePullRequest" in a for a in c)]
        return head, calls, (self.job / "REPORT.md").read_text().split("## 병합")[-1]

    def test_an_easy_pr_with_nothing_left_goes_in_the_merge_queue_on_the_reviewed_head(self):
        head, calls, note = self.queued("리뷰한 head와 되돌리기: {head}, 되돌리기 쉬움\n고친 것: P0 0, P1 0, P2 0\n남은 지적: 없음\n")
        self.assertEqual(calls, [["api", "graphql", "-f", calls[0][3], "-f", "id=PR_7", "-f", f"sha={head}"]])
        self.assertIn("expectedHeadOid:$sha", calls[0][3])  # the queue refuses any other head
        self.assertIn(f"`{head}`를 머지 큐에 넣었다", note)
        self.setUp()  # a review commit outside the trust paths is queued too, on its own head
        head, calls, note = self.queued("리뷰한 head와 되돌리기: {head}, 되돌리기 쉬움\n남은 지적: 없음\n", review_files=("fix.py",))
        self.assertEqual(calls[0][-1], f"sha={head}")

    def test_seen_records_the_bodies_the_worker_judges(self):
        self.queued("리뷰한 head와 되돌리기: {head}, 되돌리기 쉬움\n남은 지적: 없음\n")
        judged = json.loads((self.job / "seen.json").read_text(encoding="utf-8"))
        bodies = json.loads((self.job / "seen-bodies.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(bodies), judged)
        for note, body in bodies.items():  # each body hashes to its entry: what the worker reads is what was recorded
            self.assertEqual(note.rsplit(":", 1)[1], hashlib.sha256(body.encode()).hexdigest())

    def test_a_pr_that_needs_a_person_is_not_queued(self):
        easy = "리뷰한 head와 되돌리기: {head}, 되돌리기 쉬움\n남은 지적: 없음\n"
        cases = [("리뷰한 head와 되돌리기: {head}, 되돌리기 어려움\n남은 지적: 없음\n", {}, "리뷰가 쉬움으로"),
                 ("리뷰한 head와 되돌리기: {head}, 되돌리기 쉬움\n남은 지적: [Spec] P2 y, a.py:3\n", {}, "남은 지적이"),
                 ("리뷰한 head와 되돌리기: {head}, 되돌리기 쉬움\n", {}, "남은 지적이"),
                 ("리뷰한 head와 되돌리기: " + "a" * 40 + ", 되돌리기 쉬움\n남은 지적: 없음\n", {}, "CI가 통과한 head와"),
                 (easy, {"easy_pr": None}, "쉬움을 확인하지"),
                 (easy + "남은 지적: [Spec] P1 x, a.py:1\n", {}, "남은 지적이"),  # every line, not the first
                 (easy, {"review_files": ("docs/AGENTS.md",)}, "민감한 파일"),  # a review commit after the proof
                 (easy, {"review_files": (".claude/settings.json",)}, "민감한 파일"),
                 ("리뷰한 head와 되돌리기: {head}, 쉬움이 아님\n남은 지적: 없음\n", {}, "리뷰가 쉬움으로"),
                 (easy + "리뷰한 head와 되돌리기: {head}, 어려움\n", {}, "리뷰가 쉬움으로"),  # a second verdict
                 (easy, {"checks": "[]"}, "체크 없음"),
                 (easy, {"checks": '[{"name": "CI", "status": "COMPLETED", "conclusion": "STALE"}]'}, "체크 없음"),  # not a pass
                 (easy, {"base": "release"}, "base가"),
                 (easy, {"office": "https://github.com/O/R.git"}, "repo-office 자신의 PR"),  # the PR is in the office repository
                 (easy, {"office": ""}, "repo-office 자신의 PR"),  # no office origin: it cannot tell, so a person merges  # a person moved the PR: the trust check would see the wrong base  # no CI is not a green CI
                 (easy, {"branch": "feature/x"}, "PR 브랜치가"),  # pr.txt names a PR the engine did not push
                 (easy, {"url": "https://github.com/other/r/pull/7"}, "push한 저장소"),  # same branch name, another repository
                 (easy, {"push": "https://github.com/other/r.git"}, "push한 저장소"),  # pushurl/pushInsteadOf send it elsewhere
                 (easy, {"late": '[2, "P1: wrong"]\n'}, "판정하지 않은"),  # a comment or review after the worker's record
                 (easy, {"notes": '[1, "LGTM"]\n', "late": '[1, "LGTM. P1: on second look"]\n'}, "판정하지 않은"),  # an edit
                 (easy, {"notes": '[1, "LGTM"]\n', "late": ""}, None),  # control: the same notes are judged
                 (easy, {"judged": False}, "seen.json"),
                 (easy, {"redesign": True}, "승인 뒤 design.md")]  # the last check, right before the request
        for review, state, why in cases:
            with self.subTest(review=review, state=state):
                self.setUp()
                head, calls, note = self.queued(review, **state)
                if why is None:
                    self.assertEqual(len(calls), 1)
                    continue
                self.assertEqual(calls, [])
                self.assertIn("사람이 병합한다", note)
                self.assertIn(why, note)

    def test_approve_refuses_outside_an_approval_stage(self):
        self.engine.run("search", FakeWorker(self.job))
        self.engine.approve("search")
        self.engine.run("search", FakeWorker(self.job))
        self.assertEqual(self.engine.approve("search"), 2)

    def refused(self, command, name):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = command(name)
        self.assertEqual(code, 2)
        self.assertEqual(len(err.getvalue().splitlines()), 1)
        self.assertNotIn("Traceback", err.getvalue())

    def test_a_missing_task_folder_is_a_one_line_error(self):
        for command in (self.engine.run, self.engine.approve):
            self.refused(command, "nope")
        self.assertFalse((Path(self.tmp.name) / "nope").exists())

    def test_a_missing_task_json_is_a_one_line_error(self):
        (self.job / "task.json").unlink()
        for command in (self.engine.run, self.engine.approve):
            self.refused(command, "search")
        self.assertFalse((self.job / "state.json").exists())
        self.assertFalse((self.job / "BLOCKED.md").exists())

    def test_approve_without_state_json_is_a_one_line_error(self):
        self.refused(self.engine.approve, "search")

    def test_a_failed_check_goes_back_to_implement_then_stops_on_the_second_failure(self):
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        (self.job / "repo/pass").unlink()
        self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
        self.assertEqual([c[0] for c in worker.calls], ["design", "implement", "implement"])
        self.assertIn("twice in a row", (self.job / "BLOCKED.md").read_text())
        self.assertEqual(self.state()["fails"], {})

    def test_every_failure_is_kept_with_its_reason_time_exit_and_log(self):
        worker = FakeWorker(self.job)
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", worker)
            self.engine.approve("search")
            (self.job / "repo/pass").unlink()
            self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
        failures = self.state()["failures"]
        self.assertEqual([(f["stage"], f["exit"], f["log"]) for f in failures],
                         [("check", 1, "test.fail-1.log"), ("check", 1, "test.fail-2.log")])
        self.assertIn("exit 1", failures[0]["reason"])
        self.assertEqual(failures[0]["model"], self.state()["models"][1]["model"])  # the implement run it checked
        self.assertTrue(all((self.job / f["log"]).exists() for f in failures))
        self.assertLessEqual(failures[0]["at"], failures[1]["at"])
        for m in self.state()["models"]:
            self.assertLessEqual(m["start"], m["end"])
        self.assertEqual({t["stage"] for t in self.state()["timings"]}, {"check"})
        # An answer clears fails for two fresh tries; the failures list keeps the earlier ones.
        intent = self.job / "INTENT.md"
        intent.write_text(intent.read_text() + "- 답\n", encoding="utf-8")
        os.utime(intent, (time.time() + 5, time.time() + 5))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
        self.assertEqual(len(self.state()["failures"]), 4)
        self.assertTrue((self.job / "test.fail-4.log").exists())
        (self.job / "BLOCKED.md").unlink()  # a block's own reason comes first
        self.assertEqual(self.engine.job_status(self.job, ask=False)["note"], "")  # the answer cleared fails: nothing runs again
        self.engine.save(self.job / "state.json", {**self.state(), "fails": {"check": 1}})
        self.assertIn("직전 실패", self.engine.job_status(self.job, ask=False)["note"])

    def test_a_block_from_hc_is_kept_apart_from_failed_tries(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", lambda *a: "You've hit your 5-hour limit")
        failure, = self.state()["failures"]
        self.assertEqual((failure["stage"], failure["blocked"]), ("design", True))
        self.assertEqual(self.state()["fails"], {})

    def test_a_state_json_from_before_the_failure_log_still_runs(self):
        self.engine.save(self.job / "state.json", {"stage": "design", "since": 0, "fails": {}, "sessions": [],
                                                   "models": [{"stage": "design", "model": "opus"}]})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("search", FakeWorker(self.job)), self.engine.WAIT)
        self.assertEqual(self.engine.job_status(self.job, ask=False)["note"], "")
        self.assertIn("start", self.state()["models"][-1])

    def test_uncommitted_changes_fail_the_check(self):
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        (self.job / "repo/left.txt").write_text("x")
        self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
        self.assertIn("left.txt", (self.job / "check.md").read_text())

    def test_files_the_test_writes_do_not_fail_the_check(self):
        self.fake_github()
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        task = json.loads((self.job / "task.json").read_text())
        task["test"] = "touch cache.pyc"
        (self.job / "task.json").write_text(json.dumps(task))
        self.engine.run("search", worker)
        self.assertEqual(self.state()["stage"], "review")  # past the check and the draft
        self.assertIn("커밋하지 않은 변경: 없음", (self.job / "check.md").read_text())

    def test_every_pr_is_marked_ready_and_only_a_hard_one_asks_the_bot(self):
        # Through run(): the Ready call depends on the approved design and the body the engine read.
        cases = [("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", True),
                 ("Door: 되돌리기 어려움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", False),
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 어려움\n신뢰 경계: 없음", False),
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: .claude/", False),
                 ("Door: 되돌리기 쉬움\nDoor: 되돌리기 어려움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", False),
                 ("되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", False),
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움", False),
                 ("## Door\n\nDoor: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", True),  # a heading is no claim
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n- **Door**: 되돌리기 어려움\n신뢰 경계: 없음", False),
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음\n> 신뢰 경계: 없음", False),
                 ("Door: 되돌리기 쉬움", "**Door:** 되돌리기 쉬움\n신뢰 경계: 없음", False),
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", ".claude/settings.json"),
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", "docs/AGENTS.md"),
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", "CLAUDE.local.md"),
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", ".claude/설정.json"),  # git quotes it by default
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", "a\nb/.claude/x.json"),  # . in a regex skips \n
                 ("Door: 되돌리기 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음", "AGENTS.md->notes.md")]  # a rename out
        for design, body, *rest in cases:
            ready, guarded = (rest[0], None) if isinstance(rest[0], bool) else (False, rest[0])
            with self.subTest(design=design, body=body):
                self.setUp()
                git = ["git", "-C", str(self.job / "repo")]
                subprocess.run([*git, "config", "core.quotePath", "true"], check=True)  # git's default, whatever ~/.gitconfig says
                source, _, target = (guarded or "").partition("->")
                if target:  # the base has the trust file; the branch renames it away
                    (self.job / "repo" / source).write_text("x\n", encoding="utf-8")
                    subprocess.run([*git, "add", source], check=True)
                    subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
                _, draft, gh = self.fake_github()

                class Worker(FakeWorker):
                    def __call__(w, name, cwd, argv, message):
                        super().__call__(name, cwd, argv, message)
                        stage = name.rsplit("-", 1)[1]
                        if stage == "design":
                            (self.job / "design.md").write_text(f"# d\n\n{design}\n", encoding="utf-8")
                        if stage == "implement":
                            (self.job / "pr-body.md").write_text(f"# fix: x\n\n{body}\n", encoding="utf-8")
                            if target:
                                subprocess.run([*git, "mv", source, target], check=True)
                                subprocess.run([*git, "commit", "-q", "-m", "g"], check=True)
                            elif guarded:  # the body says no trust boundary; the commit touches one
                                path = self.job / "repo" / guarded
                                path.parent.mkdir(parents=True, exist_ok=True)
                                path.write_text("x\n", encoding="utf-8")
                                subprocess.run([*git, "add", "-f", guarded], check=True)  # -f: ignored names commit too
                                subprocess.run([*git, "commit", "-q", "-m", "g"], check=True)

                worker = Worker(self.job)
                if self.engine.run("search", worker) == self.engine.WAIT:
                    # only the current wording approves a design: the old Door: wording waits for a person
                    self.assertFalse(self.engine.claims(design.encode(), self.engine.DOOR_CLAIM, {"되돌리기: 쉬움"}))
                    self.assertEqual(self.engine.approve("search"), 0)
                    self.engine.run("search", worker)
                else:  # an easy design needs no person: the approve stage recorded its bytes itself
                    self.assertEqual(self.state()["auto_approved"]["approve"], self.engine.sha(self.job / "design.md"))
                # Every PR is opened ready, so no draft-to-ready event runs the PR workflows twice;
                # the proof of easy picks the review's model (and the report's note) only.
                self.assertEqual(draft.read_text(), "false")
                self.assertNotIn(["pr", "ready", "https://github.com/O/R/pull/7"], gh())
                self.assertEqual(self.state()["stage"], "review")
                self.assertEqual(self.state().get("easy_pr") == self.state()["checked"], bool(ready))
                # POLICY §3: an easy PR never asks the review bot; every other PR asks once.
                self.assertEqual([c[:2] for c in gh()].count(["pr", "comment"]), 0 if ready else 1)

    def test_a_reapproved_design_drops_the_earlier_proof_of_easy(self):
        # Through run(): the PR stays open with the same commit, so the draft stage never proves easy again.
        _, _, gh = self.fake_github()
        design = ["되돌리기: 쉬움"]  # the new lines, through run()

        class Worker(FakeWorker):
            def __call__(w, name, cwd, argv, message):
                super().__call__(name, cwd, argv, message)
                stage = name.rsplit("-", 1)[1]
                if stage == "design":
                    (self.job / "design.md").write_text(f"# d\n\n{design[0]}\n", encoding="utf-8")
                if stage == "implement":
                    (self.job / "pr-body.md").write_text("# fix: x\n\n되돌리기: 쉬움\n민감한 파일: 없음\n", encoding="utf-8")
                    if design[0].endswith("어려움"):  # the new design needs no code change: no new commit
                        subprocess.run(["git", "-C", str(self.job / "repo"), "reset", "-q", "--soft", "HEAD~1"], check=True)

        worker = Worker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        self.engine.run("search", worker)
        self.assertEqual(self.state()["easy_pr"], self.state()["checked"])
        self.assertNotIn(["pr", "comment"], [c[:2] for c in gh()])
        (self.job / "BLOCKED.md").unlink()  # the review stage's pin check, not part of this case
        design[0] = "되돌리기: 어려움"
        (self.job / "design.md").write_text(f"# d\n\n{design[0]}\n", encoding="utf-8")
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertEqual(self.engine.approve("search"), 0)
        self.engine.run("search", worker)
        self.assertEqual(self.state()["stage"], "review")
        self.assertFalse(self.engine.proven_easy(self.state()))
        self.assertEqual([c[:2] for c in gh()].count(["pr", "comment"]), 1)  # the hard design asks the bot once

    def test_draft_stage_proves_easy_only_from_the_approved_design(self):
        # The same easy text, but not the bytes a person approved: not easy.
        _, _, gh = self.fake_github()
        (self.job / "design.md").write_text("Door: 되돌리기 쉬움\n", encoding="utf-8")
        body = "# fix: x\n\nDoor: 되돌리기 쉬움\n신뢰 경계: 없음\n".encode()
        task = json.loads((self.job / "task.json").read_text())
        head = subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        stage = {"id": "draft", "body": "pr-body.md", "out": "pr.txt", "easy": "design.md"}
        seen = {}
        self.assertIsNone(self.engine.open_draft(self.job, task, stage, body, head, "0" * 64, seen))
        self.assertEqual(seen, {})
        (self.job / "pr.txt").unlink()
        approved = hashlib.sha256((self.job / "design.md").read_bytes()).hexdigest()
        self.assertIsNone(self.engine.open_draft(self.job, task, stage, body, head, approved, seen))
        self.assertEqual(seen, {"easy": True})
        self.assertEqual([c[:2] for c in gh()], [["pr", "create"], ["pr", "create"]])

    def test_draft_stage_reads_easy_only_from_the_approved_design(self):
        # A process the tested code left behind rewrites design.md after the engine's approval check: no Ready.
        _, _, gh = self.fake_github()

        class Worker(FakeWorker):
            def __call__(w, name, cwd, argv, message):
                super().__call__(name, cwd, argv, message)
                if name.endswith("-design"):
                    (self.job / "design.md").write_text("# d\n\nDoor: 되돌리기 어려움\n", encoding="utf-8")
                if name.endswith("-implement"):
                    (self.job / "pr-body.md").write_text("# fix: x\n\nDoor: 되돌리기 쉬움\n신뢰 경계: 없음\n", encoding="utf-8")

        real = self.engine.read_body

        def rewrite_before_the_read(path):
            if Path(path).name == "design.md":
                Path(path).write_text("# d\n\nDoor: 되돌리기 쉬움\n", encoding="utf-8")
            return real(path)

        worker = Worker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        with unittest.mock.patch.object(self.engine, "read_body", rewrite_before_the_read):
            self.engine.run("search", worker)
        self.assertEqual([c[:2] for c in gh()].count(["pr", "create"]), 1)  # the draft stage got to the read
        self.assertNotIn(["pr", "ready"], [c[:2] for c in gh()])

    def test_draft_stage_opens_one_pr_and_blocks_instead_of_forcing(self):
        bare, _, gh = self.fake_github()
        stage = {"id": "draft", "body": "pr-body.md", "out": "pr.txt"}
        task = json.loads((self.job / "task.json").read_text())
        body = self.job / "pr-body.md"
        head = lambda: subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"],  # noqa: E731
                                      capture_output=True, text=True).stdout.strip()
        draft = lambda t=task: self.engine.open_draft(self.job, t, stage, self.engine.read_body(body), head())  # noqa: E731
        for text in (None, "no title line\n", "# \n"):
            if text:
                body.write_text(text, encoding="utf-8")
            self.assertIn("# <PR title>", draft())
        self.assertEqual(gh(), [])
        # A branch of that name with other history on the remote: no force push, no PR.
        other = Path(self.tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", "theirs"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "HEAD:refs/heads/office/search"], check=True, capture_output=True)
        body.write_text("# fix: x\n", encoding="utf-8")
        self.assertIn("git push failed", draft())
        self.assertEqual(gh(), [])
        # Over GitHub's body size: not posted.
        body.write_text("# fix: x\n" + "x" * 60001, encoding="utf-8")
        self.assertIn("at most 60000 bytes", draft())
        body.unlink()
        # A branch that changes the workflows waits for a person: a push runs them with the repository's secrets.
        # The base is the remote's commit: the worker can point its own origin/main at HEAD.
        repo = self.job / "repo"
        (repo / ".github").mkdir()
        (repo / ".github/ci.yml").write_text("on: push\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", ".github"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "ci"], check=True)
        subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
        body.write_text("# fix: x\n", encoding="utf-8")
        self.assertIn(".github", draft({**task, "base": "origin/main"}))
        self.assertIn("no branch gone", draft({**task, "base": "origin/gone"}))
        self.assertEqual(gh(), [])
        # A URL already in pr.txt (an earlier run, or a person) is kept: nothing is pushed or opened.
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/1\n", encoding="utf-8")
        self.assertIsNone(draft())
        self.assertEqual(gh(), [])

    def test_draft_stage_opens_the_pr_after_the_remote_base_moved(self):
        bare, _, gh = self.fake_github()
        other = Path(self.tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(bare), str(other)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", "someone else's change"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "HEAD:refs/heads/main"], check=True, capture_output=True)
        # The template's 그림 line as a Mermaid block: it reaches the PR byte for byte, so GitHub draws it.
        text = "## Summary\n그림:\n```mermaid\nflowchart LR\n  a[\"# a\"] --> b\n```\n완료 기준: x\n"
        raw = f"# fix: x\n\n{text}".encode()
        (self.job / "pr-body.md").write_bytes(raw)
        task = json.loads((self.job / "task.json").read_text())
        head = subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertIsNone(self.engine.open_draft(self.job, task, {"id": "draft", "body": "pr-body.md", "out": "pr.txt"}, raw, head))
        [call] = gh()
        self.assertEqual(call[:2], ["pr", "create"])
        self.assertEqual(call[call.index("--title") + 1], "fix: x")
        self.assertEqual(call[call.index("--body") + 1], text)

    def test_draft_stage_pushes_only_the_checked_commit(self):
        bare, _, gh = self.fake_github()
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        self.engine.run("search", worker)
        # A rerun after a blocked draft: a commit made since the check goes through the check again before the push.
        (self.job / "pr.txt").unlink()
        (self.job / "BLOCKED.md").unlink(missing_ok=True)  # the review blocked: setUp's clone is not pinned
        self.engine.save(self.job / "state.json", {**self.state(), "stage": "draft"})
        repo = self.job / "repo"
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", "after the check"], check=True)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.engine.run("search", worker)
        self.assertIn(head, (self.job / "check.md").read_text())
        self.assertEqual(self.state()["checked"], head)
        self.assertEqual(subprocess.run(["git", "-C", str(bare), "rev-parse", "office/search"],
                                        capture_output=True, text=True).stdout.strip(), head)
        self.assertEqual([c[:2] for c in gh()].count(["pr", "create"]), 2)

    def test_draft_stage_posts_the_bytes_it_compared(self):
        _, _, gh = self.fake_github()
        real, reads = self.engine.read_body, []

        def swap_after_the_draft_read(path):
            raw = real(path)
            reads.append(path)
            if len(reads) == 2:  # the check stage read it, then the draft stage: change it before anything reads again
                Path(path).write_text("# fix: swapped\n", encoding="utf-8")
            return raw

        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        with unittest.mock.patch.object(self.engine, "read_body", swap_after_the_draft_read):
            self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        titles = [c[c.index("--title") + 1] for c in gh() if c[:2] == ["pr", "create"]]
        self.assertEqual(titles, ["fix: x"])

    def test_draft_stage_pushes_the_commit_it_compared(self):
        # HEAD moves after the draft stage compared it with the checked commit: the push still sends the checked one.
        bare, _, gh = self.fake_github()
        repo, real, reads = self.job / "repo", self.engine.read_body, []

        def commit_after_the_comparison(path):
            if Path(path).name != "pr-body.md":  # the draft stage also reads design.md for its Ready
                return real(path)
            reads.append(path)
            if len(reads) == 1:  # the draft stage reads the body after its HEAD comparison
                subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "late"], check=True)
            return real(path)

        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        with unittest.mock.patch.object(self.engine, "read_body", commit_after_the_comparison):
            self.engine.run("search", worker)
        rev = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()  # noqa: E731
        self.assertEqual(len(reads), 1)
        self.assertNotEqual(rev("-C", str(repo), "rev-parse", "HEAD"), self.state()["checked"])  # the race happened
        self.assertEqual(rev("-C", str(bare), "rev-parse", "office/search"), self.state()["checked"])
        self.assertEqual([c[:2] for c in gh()].count(["pr", "create"]), 1)

    def test_the_engine_posts_the_review_body_only_when_it_is_written(self):
        _, draft, gh = self.fake_github()
        draft.write_text("false\n", encoding="utf-8")
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        self.engine.run("search", worker)  # opens the draft; the review blocks: setUp's clone is not pinned
        office = Path(self.tmp.name) / "office"
        subprocess.run(["git", "init", "-q", str(office)], check=True)
        subprocess.run(["git", "-C", str(office), "remote", "add", "origin", "https://github.com/me/office.git"], check=True)
        self.engine.BASELINE = office
        body = self.job / "review-body.md"
        edits = lambda: [c for c in gh() if c[:2] == ["pr", "edit"]]  # noqa: E731
        # No file, an empty one or one over the size limit is not posted.
        for make in (lambda: None, lambda: body.write_text("", encoding="utf-8"), lambda: body.write_text("x" * 60001, encoding="utf-8")):
            def review(name, cwd, argv, message, make=make):
                worker(name, cwd, argv, message)
                body.unlink()
                make()
            self.engine.save(self.job / "state.json", {**self.state(), "stage": "review"})
            (self.job / "BLOCKED.md").unlink(missing_ok=True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.engine.run("search", review), self.engine.BLOCKED)
            self.assertIn("review-body.md", (self.job / "BLOCKED.md").read_text())
            self.assertEqual(edits(), [])
            body.unlink(missing_ok=True)
        # A person fixed the file and cleared BLOCKED.md: the post runs again, not the review.
        self.assertEqual(self.state()["stage"], "post")
        body.write_text("## Review\nfixed\n", encoding="utf-8")
        (self.job / "BLOCKED.md").unlink()
        calls = len(worker.calls)
        self.assertEqual(self.engine.run("search", worker), self.engine.DONE)
        self.assertEqual(len(worker.calls), calls)
        self.assertEqual(edits(), [["pr", "edit", "https://github.com/O/R/pull/7", "--body-file", "-", "## Review\nfixed\n"]])

    def test_a_review_body_left_from_an_earlier_try_is_not_posted(self):
        _, _, gh = self.fake_github()
        (self.job / "pr.txt").write_text("https://github.com/O/R/pull/7\n", encoding="utf-8")
        (self.job / "review-body.md").write_text("stale\n", encoding="utf-8")
        office = Path(self.tmp.name) / "office"
        subprocess.run(["git", "init", "-q", str(office)], check=True)
        subprocess.run(["git", "-C", str(office), "remote", "add", "origin", "https://github.com/me/office.git"], check=True)
        self.engine.BASELINE = office
        (self.job / "design.md").write_text("d\n", encoding="utf-8")
        self.engine.save(self.job / "state.json", {"stage": "review", "since": 0, "fails": {}, "sessions": [],
                                                   "approved": {"approve": self.engine.sha(self.job / "design.md")}})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("search", lambda *a: (self.job / "review.md").write_text("r\n") and None),
                             self.engine.BLOCKED)
        self.assertIn("review-body.md", (self.job / "BLOCKED.md").read_text())  # the post blocked, not the review
        self.assertEqual([c for c in gh() if c[:2] == ["pr", "edit"]], [])

    def test_the_review_body_is_posted_only_to_a_github_pr_url(self):
        # gh runs with the owner's login: pr.txt must name a GitHub PR, not an option or another host.
        # Starts at the post stage: the review stage already refuses a pr.txt it cannot pin to a repository.
        _, _, gh = self.fake_github()
        (self.job / "design.md").write_text("d\n", encoding="utf-8")
        (self.job / "review-body.md").write_text("## Review\nok\n", encoding="utf-8")
        for url in ("--repo=o/r\n", "https://example.com/o/r/pull/7\n", ""):
            (self.job / "pr.txt").write_text(url, encoding="utf-8")
            (self.job / "BLOCKED.md").unlink(missing_ok=True)
            self.engine.save(self.job / "state.json", {"stage": "post", "since": 0, "fails": {}, "sessions": [],
                                                       "approved": {"approve": self.engine.sha(self.job / "design.md")}})
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.engine.run("search", FakeWorker(self.job)), self.engine.BLOCKED)
            self.assertIn("pr.txt", (self.job / "BLOCKED.md").read_text())
            self.assertEqual([c for c in gh() if c[:2] == ["pr", "edit"]], [])

    def at_post(self):
        """A reviewed PR whose branch office/search is on origin, at the post stage. Returns the remote's tip and gh calls."""
        bare, _, gh = self.fake_github()
        subprocess.run(["git", "-C", str(self.job / "repo"), "push", "-q", "origin", "HEAD:refs/heads/office/search"], check=True)
        (self.job / "design.md").write_text("d\n", encoding="utf-8")
        (self.job / "review-body.md").write_text("## Review\nok\n", encoding="utf-8")
        (self.job / "pr.txt").write_text("https://github.com/O/R/pull/7\n", encoding="utf-8")
        self.engine.save(self.job / "state.json", {"stage": "post", "since": 0, "fails": {}, "sessions": [],
                                                   "approved": {"approve": self.engine.sha(self.job / "design.md")}})
        tip = lambda ref="refs/heads/office/search": subprocess.run(  # noqa: E731
            ["git", "-C", str(bare), "rev-parse", "-q", "--verify", ref], capture_output=True, text=True).stdout.strip()
        self.gh_calls = gh
        return tip, lambda: [c for c in gh() if c[:2] == ["pr", "edit"]]

    def review_commit(self, *files):
        repo = self.job / "repo"
        for name in files:
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A", "--", *files], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "review fix"], check=True)
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def post(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine.run("search", FakeWorker(self.job))

    def test_the_engine_pushes_the_review_commits_then_posts_the_body(self):
        # The worker's push is denied: the engine pushes the review's commits for it.
        tip, edits = self.at_post()
        head = self.review_commit("fix.py")
        self.assertEqual(self.post(), self.engine.DONE)
        self.assertEqual(tip(), head)
        self.assertEqual(len(edits()), 1)

    def test_a_red_ci_blocks_with_the_failed_checks(self):
        # bg-600: the check stage passed locally and CI failed; the engine said done and nobody heard of it.
        self.at_post()
        self.checks.write_text('[{"name": "smoke", "status": "COMPLETED", "conclusion": "FAILURE", "detailsUrl": "https://ci/1"},'
                               ' {"name": "CI", "status": "IN_PROGRESS"}]', encoding="utf-8")  # a failure wins over a running check
        self.assertEqual(self.post(), self.engine.BLOCKED)
        self.assertIn("smoke https://ci/1", (self.job / "BLOCKED.md").read_text())
        self.assertEqual(self.state()["stage"], "ci")
        self.assertTrue((self.job / "REPORT.md").exists())  # the post stage already wrote it
        # A person reran or fixed CI and the leader answered: the next run asks again and ends.
        self.checks.write_text('[{"name": "smoke", "status": "COMPLETED", "conclusion": "SUCCESS"}]', encoding="utf-8")
        time.sleep(0.01)
        (self.job / "INTENT.md").write_text("# search\n\n- CI를 다시 돌림\n", encoding="utf-8")
        self.assertEqual(self.post(), self.engine.DONE)
        self.assertIn("통과", (self.job / "REPORT.md").read_text().split("## CI")[-1])

    def test_a_pr_without_checks_ends_after_the_wait(self):
        self.at_post()
        self.checks.write_text("[]", encoding="utf-8")
        self.engine.CI_NONE = 0
        self.assertEqual(self.post(), self.engine.DONE)
        self.assertIn("체크 없음", (self.job / "REPORT.md").read_text())

    def test_a_pr_without_checks_yet_is_waited_on(self):
        # GitHub starts the checks a while after the push: no checks before CI_NONE is not yet "no CI".
        self.at_post()
        self.checks.write_text("[]", encoding="utf-8")
        self.engine.CI_LIMIT = 0.05
        self.assertEqual(self.post(), self.engine.BLOCKED)
        self.assertIn("did not finish", (self.job / "BLOCKED.md").read_text())
        self.assertNotIn("## CI", (self.job / "REPORT.md").read_text())

    def test_checks_of_an_older_head_are_not_waited_on(self):
        self.at_post()
        self.engine.CI_LIMIT, self.engine.CI_MOVED = 0.05, 3600  # within the grace GitHub may still show the old head
        real = subprocess.run
        # The old head's checks are green: only the head comparison keeps them from ending the run.
        green = '[{"name": "CI", "status": "COMPLETED", "conclusion": "SUCCESS"}]'
        old = lambda argv, **kw: (subprocess.CompletedProcess(argv, 0, f'{{"headRefOid": "old", "statusCheckRollup": {green}}}', "")  # noqa: E731
                                  if "headRefOid,statusCheckRollup" in argv else real(argv, **kw))
        with unittest.mock.patch.object(self.engine.subprocess, "run", side_effect=old):
            self.assertEqual(self.post(), self.engine.BLOCKED)
        self.assertIn("did not finish", (self.job / "BLOCKED.md").read_text())

    def test_a_pr_head_that_moved_blocks_at_once_with_the_reason(self):
        # PR #66 review P1: a person pushed after the engine; waiting the whole hour then saying "did not finish" was wrong.
        self.at_post()
        self.engine.CI_LIMIT, self.engine.CI_MOVED = 1, 0  # without the check this ends as "did not finish"
        real = subprocess.run
        green = '[{"name": "CI", "status": "COMPLETED", "conclusion": "SUCCESS"}]'
        moved = lambda argv, **kw: (subprocess.CompletedProcess(argv, 0, f'{{"headRefOid": "newer", "statusCheckRollup": {green}}}', "")  # noqa: E731
                                    if "headRefOid,statusCheckRollup" in argv else real(argv, **kw))
        started = time.time()
        with unittest.mock.patch.object(self.engine.subprocess, "run", side_effect=moved):
            self.assertEqual(self.post(), self.engine.BLOCKED)
        self.assertLess(time.time() - started, 60)
        blocked = (self.job / "BLOCKED.md").read_text()
        self.assertIn("PR head moved", blocked)
        self.assertIn("newer", blocked)
        self.assertNotIn("## CI", (self.job / "REPORT.md").read_text())  # green checks of another head are not this PR's

    def test_easy_reads_the_new_lines_and_the_old_ones(self):
        easy = lambda d, b: self.engine.easy(d.encode(), b.encode())  # noqa: E731
        self.assertTrue(easy("되돌리기: 쉬움", "되돌리기: 쉬움\n민감한 파일: 없음"))
        self.assertTrue(easy("Door: 되돌리기 쉬움", "되돌리기: 쉬움\n민감한 파일: 없음"))  # a design approved before the rename
        self.assertTrue(easy("되돌리기: 쉬움", "Door: 되돌리기 쉬움\n신뢰 경계: 없음"))  # a product still on the old template
        # The new words are ordinary Korean: prose without a colon is no claim, and 되돌리는 법 is another field.
        self.assertTrue(easy("되돌리기: 쉬움\n문서만 바꿔 되돌리기 쉽다.", "되돌리기: 쉬움\n되돌리는 법: revert\n민감한 파일: 없음\n민감한 파일을 건드리지 않는다"))
        for design, body in [("되돌리기: 어려움", "되돌리기: 쉬움\n민감한 파일: 없음"),
                             ("되돌리기: 쉬움", "되돌리기: 쉬움\n민감한 파일: `.claude/`"),
                             ("되돌리기: 쉬움", "되돌리기: 쉬움\n민감한 파일: 없음\n신뢰 경계: 없음"),  # two claims
                             ("되돌리기: 쉬움", "되돌리기: 쉬움\n- 되돌리기: 어려움\n민감한 파일: 없음"),
                             ("되돌리기: 쉬움", "**되돌리기:** 쉬움\n민감한 파일: 없음"),
                             ("되돌리기 쉬움", "되돌리기: 쉬움\n민감한 파일: 없음"),  # no claim in the design
                             ("되돌리기: 쉬움", "되돌리기: 쉬움\nDoor 어려움\n민감한 파일: 없음"),
                             # The word with a colon after it anywhere on the line is a claim too.
                             ("되돌리기: 쉬움\n되돌리기 판정: 어려움", "되돌리기: 쉬움\n민감한 파일: 없음"),
                             ("되돌리기: 쉬움", "되돌리기: 쉬움\n참고: 되돌리기: 어려움\n민감한 파일: 없음"),
                             ("되돌리기: 쉬움", "되돌리기: 쉬움\n민감한 파일: 없음\n참고: 민감한 파일: `.claude/`"),
                             ("되돌리기: 쉬움", "되돌리기 (판정): 어려움\n되돌리기: 쉬움\n민감한 파일: 없음"),
                             ("되돌리기: 쉬움", "되돌리기: 쉬움\n민감한 파일: 없음\n민감한 파일 목록: `.claude/`")]:
            with self.subTest(design=design, body=body):
                self.assertFalse(easy(design, body))

    def test_the_review_line_is_read_in_both_names(self):
        sha = "a" * 40
        for line, easy in [(f"리뷰한 head와 되돌리기: {sha}, 쉬움", True), (f"리뷰한 head와 되돌리기: {sha}, 어려움", False),
                           (f"리뷰한 head와 Door: {sha}, 되돌리기 쉬움", True), (f"리뷰한 head와 Door: {sha}, 되돌리기 어려움", False),
                           ("리뷰 끝", False)]:
            with self.subTest(line=line):
                (self.job / "review.md").write_text(line + "\n", encoding="utf-8")
                self.assertIs(self.engine.review_says_easy(self.job), easy)

    def test_a_review_that_raises_door_asks_the_bot_once_after_the_body(self):
        # The ready stage skipped the bot for a PR it proved easy; the review saw it hard (FakeWorker writes 어려움).
        tip, edits = self.at_post()
        (self.job / "review.md").write_text("리뷰한 head와 Door: " + "a" * 40 + ", 되돌리기 어려움\n", encoding="utf-8")
        self.assertEqual(self.post(), self.engine.DONE)
        calls = [c[:2] for c in self.gh_calls()]
        self.assertEqual(calls.count(["pr", "comment"]), 1)
        self.assertLess(calls.index(["pr", "edit"]), calls.index(["pr", "comment"]))
        self.assertIn("사람이 diff를 읽고 병합한다", (self.job / "REPORT.md").read_text())
        self.engine.save(self.job / "state.json", {**self.state(), "stage": "post"})
        self.post()  # a second post never asks again
        self.assertEqual([c[:2] for c in self.gh_calls()].count(["pr", "comment"]), 1)

    def test_a_review_that_keeps_door_easy_asks_no_bot(self):
        tip, edits = self.at_post()
        self.engine.save(self.job / "state.json", {**self.state(), "easy_pr": "x", "checked": "x"})
        (self.job / "review.md").write_text("리뷰한 head와 Door: " + "a" * 40 + ", 되돌리기 쉬움\n", encoding="utf-8")

        class Easy(FakeWorker):
            def __call__(w, *a):
                return None
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("search", Easy(self.job)), self.engine.DONE)
        self.assertNotIn(["pr", "comment"], [c[:2] for c in self.gh_calls()])
        self.assertNotIn("사람이 diff를 읽고 병합한다", (self.job / "REPORT.md").read_text())

    def test_a_review_without_commits_posts_the_body_only(self):
        tip, edits = self.at_post()
        before = tip()
        self.assertEqual(self.post(), self.engine.DONE)
        self.assertEqual(tip(), before)
        self.assertEqual(len(edits()), 1)

    def test_review_commits_that_change_github_wait_for_a_person(self):
        # A pushed workflow runs with the repository's secrets: neither the commits nor the body that names them go up.
        tip, edits = self.at_post()
        before = tip()
        self.review_commit(".github/ci.yml")
        self.assertEqual(self.post(), self.engine.BLOCKED)
        self.assertIn(".github", (self.job / "BLOCKED.md").read_text())
        self.assertEqual((tip(), edits()), (before, []))

    def test_review_commits_go_only_to_the_task_branch(self):
        # The worker or its tests can rename the local branch: the engine names the remote branch itself.
        tip, edits = self.at_post()
        before_main = tip("refs/heads/main")
        subprocess.run(["git", "-C", str(self.job / "repo"), "checkout", "-q", "-B", "main"], check=True)
        head = self.review_commit("fix.py")
        self.assertEqual(self.post(), self.engine.DONE)
        self.assertEqual((tip(), tip("refs/heads/main")), (head, before_main))

    def test_review_commits_are_not_pushed_without_the_task_branch_or_over_other_history(self):
        tip, edits = self.at_post()
        bare = Path(self.tmp.name) / "origin.git"
        self.review_commit("fix.py")
        subprocess.run(["git", "-C", str(bare), "update-ref", "-d", "refs/heads/office/search"], check=True)
        self.assertEqual(self.post(), self.engine.BLOCKED)  # a person opened the PR from another branch
        self.assertIn("origin has no refs/heads/office/search", (self.job / "BLOCKED.md").read_text())
        # Someone else pushed to the branch: no force push.
        other = Path(self.tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.email=o@example.com", "-c", "user.name=o", "commit", "-q",
                        "--allow-empty", "-m", "other"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "HEAD:refs/heads/office/search"], check=True, capture_output=True)
        theirs = tip()
        (self.job / "BLOCKED.md").unlink()
        self.assertEqual(self.post(), self.engine.BLOCKED)
        self.assertIn("git push failed", (self.job / "BLOCKED.md").read_text())
        self.assertEqual((tip(), edits()), (theirs, []))

    def test_a_worker_that_writes_nothing_is_retried_once(self):
        worker = FakeWorker(self.job, skip=["design"])
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertEqual([c[0] for c in worker.calls], ["design", "design"])
        # The retry resumes the session the first try created.
        self.assertIn("--resume", worker.calls[1][1])
        blocked = FakeWorker(self.job, skip=["implement", "implement"])
        self.engine.approve("search")
        self.assertEqual(self.engine.run("search", blocked), self.engine.BLOCKED)

    def test_blocked_file_stops_the_task(self):
        (self.job / "BLOCKED.md").write_text("need a decision\n")
        worker = FakeWorker(self.job)
        self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
        self.assertEqual(worker.calls, [])

    def test_a_worker_that_blocks_keeps_its_session_for_the_next_run(self):
        def blocks(name, cwd, argv, message):
            (self.job / "BLOCKED.md").write_text("need a decision\n")

        self.assertEqual(self.engine.run("search", blocks), self.engine.BLOCKED)
        (self.job / "BLOCKED.md").unlink()
        worker = FakeWorker(self.job)
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        # The first try created the session, so a second --session-id with the same id would be refused.
        self.assertIn("--resume", worker.calls[0][1])

    def test_a_pr_of_this_repository_is_reviewed_only_from_origin_main_copies(self):
        repo = self.job / "repo"
        (repo / "tools").mkdir()
        (repo / "tools/review_preflight.py").write_text("main\n", encoding="utf-8")
        for args in (["add", "tools"], ["commit", "-q", "-m", "tools"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        self.engine.BASELINE = self.engine.OFFICE = repo  # office, repo-baseline and the product are one repository
        (self.job / "design.md").write_text("d\n", encoding="utf-8")
        self.fake_gh()  # the post stage after the review
        bare = Path(self.tmp.name) / "origin.git"  # the post stage checks the PR branch before posting
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "HEAD:refs/heads/office/search"], check=True)
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/1\n", encoding="utf-8")

        def review():
            (self.job / "BLOCKED.md").unlink(missing_ok=True)
            (self.job / "state.json").write_text(json.dumps({
                "stage": "review", "since": 0, "fails": {}, "sessions": [],
                "approved": {"approve": self.engine.sha(self.job / "design.md")}}))
            worker = FakeWorker(self.job)
            with contextlib.redirect_stdout(io.StringIO()):
                return self.engine.run("search", worker), worker.calls

        self.assertEqual(review(), (self.engine.BLOCKED, []))  # no origin/main to compare with
        subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
        code, calls = review()
        self.assertEqual((code, len(calls)), (self.engine.DONE, 1))
        (repo / "new.py").write_text("", encoding="utf-8")
        self.assertEqual(review(), (self.engine.BLOCKED, []))  # untracked file
        (repo / "new.py").unlink()
        (repo / "tools/review_preflight.py").write_text("from the PR\n", encoding="utf-8")
        self.assertEqual(review(), (self.engine.BLOCKED, []))  # uncommitted change
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-am", "pr"], check=True)
        self.assertEqual(review(), (self.engine.BLOCKED, []))  # committed on a PR branch
        self.assertIn("origin/main", (self.job / "BLOCKED.md").read_text(encoding="utf-8"))

    def test_review_is_pinned_unless_the_pr_provably_belongs_to_another_repository(self):
        engine, other = self.engine, Path(self.tmp.name) / "office"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        task = {"repo": str(self.job / "repo")}
        engine.BASELINE = other  # another git folder, but no origin to tell which repository it is
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/1\n")
        self.assertIn("origin/main", engine.unpinned(self.job, task))
        engine.BASELINE = OFFICE
        own = engine.slug(subprocess.run(["git", "-C", str(OFFICE), "remote", "get-url", "origin"], capture_output=True, text=True).stdout)
        if own:  # a checkout with a GitHub origin
            self.assertIsNone(engine.unpinned(self.job, task))
        # An office checkout outside the repo-baseline clone is compared too: the PR could belong to either.
        baseline = Path(self.tmp.name) / "baseline"
        for path in (baseline, other):
            subprocess.run(["git", "init", "-q", str(path)], check=True)
            subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com",
                            "commit", "-q", "--allow-empty", "-m", "main"], check=True)
        subprocess.run(["git", "-C", str(baseline), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
        with unittest.mock.patch.object(engine, "BASELINE", baseline), unittest.mock.patch.object(engine, "OFFICE", other):
            self.assertIn(str(other), engine.unpinned(self.job, task))  # the office checkout has no origin/main
            subprocess.run(["git", "-C", str(other), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
            self.assertIsNone(engine.unpinned(self.job, task))
            (other / "new.py").write_text("", encoding="utf-8")
            self.assertIn(str(other), engine.unpinned(self.job, task))
        # An office checkout inside the clone folder is still its own repository, so it is compared too.
        nested = baseline / "office"
        subprocess.run(["git", "init", "-q", str(nested)], check=True)
        (baseline / ".git/info/exclude").write_text("office/\n", encoding="utf-8")
        with unittest.mock.patch.object(engine, "BASELINE", baseline), unittest.mock.patch.object(engine, "OFFICE", nested):
            self.assertIn(str(nested), engine.unpinned(self.job, task))  # no origin/main
        self.assertEqual(engine.slug("git@github.com:O/R.git\n"), "o/r")
        self.assertEqual(engine.slug("https://github.com/o/r/pull/1\n"), "o/r")
        self.assertIsNone(engine.slug("/tmp/r"))
        self.assertEqual(engine.slug("ssh://git@github.com/O/R.git"), "o/r")
        for other in ("https://notgithub.com/o/r.git", "https://evil.example/github.com/o/r", "https://github.com.evil/o/r",
                      "git@evil.example:github.com/o/r", "file:///github.com/o/r", "https://github.com/o"):
            with self.subTest(other=other):
                self.assertIsNone(engine.slug(other))

    def test_task_names_are_safe_folder_and_terminal_names(self):
        for bad in ("../x", "검색", "A", "a" * 25):
            self.assertEqual(self.engine.new(bad, ".", "main"), 2)

    def test_failed_hc_run_blocks_instead_of_crashing(self):
        fake = Path(self.tmp.name) / "claude"
        fake.write_text("#!/bin/sh\necho '2.1.300 (Claude Code)'\n", encoding="utf-8")
        fake.chmod(0o755)

        def hc(*args, check=True):
            if args[0] == "run":
                raise subprocess.CalledProcessError(1, ["hc", *args], stderr="name in use\n")
            return ""
        self.engine.hc = hc
        with unittest.mock.patch.dict(os.environ, {"PATH": f"{self.tmp.name}{os.pathsep}{os.environ['PATH']}"}):
            self.assertEqual(self.engine.run_worker("w", self.job, ["claude"], "hi"), "hc run failed: name in use")

    def test_a_worker_is_not_a_child_of_the_terminal_that_ran_the_engine(self):
        runs = []

        def hc(*args, check=True):
            if args[0] == "run":
                runs.append(args)
                raise subprocess.CalledProcessError(1, ["hc", *args], stderr="x")
            return ""
        self.engine.hc = hc
        self.engine.run_worker("w", self.job, ["claude"], "hi")
        self.assertIn("--no-parent", runs[0])  # a leader in hc would get notes typed into it and take the worker down

    def fake_hc(self, status, wait="done"):
        calls = []

        def hc(*args, check=True):
            calls.append(args[0])
            if args[0] == "send":
                return json.dumps({"wait": {"reason": wait}})
            if args[0] == "wait" and args[1:] != ("--timeout", "90", "w"):
                return json.dumps({"reason": wait})
            if args[0] == "get":
                return json.dumps({"status": status})
            return ""
        self.engine.hc = hc
        return calls

    def test_a_worker_that_hit_its_usage_limit_blocks_with_the_message(self):
        calls = self.fake_hc({"state": "error", "msg": "You've hit your 5-hour limit · resets 12:10am"})
        error = self.engine.run_worker("w", self.job, ["claude"], "hi")
        self.assertIn("resets 12:10am", error)
        self.assertNotIsInstance(error, self.engine.NotStarted)  # the session exists: the next try resumes it
        self.assertFalse(any(t in error for t in self.engine.TRANSIENT))  # a ten-second retry cannot clear a limit
        self.assertEqual(calls[-2:], ["kill", "rm"])
        self.fake_hc({"state": "done"})
        self.assertIsNone(self.engine.run_worker("w", self.job, ["claude"], "hi"))

    def test_a_worker_past_the_time_limit_is_closed(self):
        calls = self.fake_hc({"state": "working"}, wait="timeout")
        with unittest.mock.patch.object(self.engine, "WORKER_LIMIT", 0):
            self.assertIn("still working", self.engine.run_worker("w", self.job, ["claude"], "hi"))
        self.assertEqual(calls[-2:], ["kill", "rm"])

    def test_a_worker_that_never_started_leaves_no_session_to_resume(self):
        names = []

        def runner(name, cwd, argv, message):
            names.append(name)
            return self.engine.NotStarted("hc run failed: x")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("search", runner), self.engine.BLOCKED)
        state = self.job / "state.json"
        self.assertEqual(json.loads(state.read_text())["sessions"] if state.exists() else [], [])
        # The jobs folder's tag keeps another checkout's engine from closing this terminal as a leftover.
        # hc run failing may be passing: the engine tries once more before it blocks.
        self.assertEqual(names, [f"{self.engine.TAG}-search-design"] * 2)

    def test_a_second_run_of_the_same_task_stops(self):
        import fcntl
        with open(self.job / "run.lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(self.engine.run("search", FakeWorker(self.job)), 2)
        self.assertIn("another engine.py run search", err.getvalue())
        self.assertFalse((self.job / "state.json").exists())

    def test_test_runs_the_owner_command_in_repo(self):
        self.assertEqual(self.engine.test("search"), 0)
        (self.job / "repo/pass").unlink()
        self.assertNotEqual(self.engine.test("search"), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.engine.test("nope"), 2)

    def test_a_worker_sees_the_end_of_the_test_output_and_the_full_log(self):
        # Claude Code cut a long output in the middle, so the worker never saw the summary line (2026-11-14).
        task = json.loads((self.job / "task.json").read_text())
        task["test"] = "python3 -c \"print('head'); print('x' * 30000); print('Ran 3 tests')\"; echo err >&2; exit 3"
        (self.job / "task.json").write_text(json.dumps(task))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.test("search"), 3)
        log = self.job / "test.log"
        self.assertLess(len(out.getvalue()), 5000)
        self.assertIn("Ran 3 tests\nerr\n", out.getvalue())
        self.assertIn(f"exit 3; full output: {log}", out.getvalue())
        self.assertTrue(log.read_text().startswith("head\n" + "x" * 30000))

    def test_baseline_path_names_the_clone_else_commands_stop(self):
        office = Path(self.tmp.name) / "standalone"
        shutil.copytree(OFFICE / "pipeline", office / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))

        def baseline():
            spec = importlib.util.spec_from_file_location("standalone", office / "pipeline/engine.py")
            engine = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(engine)
            return engine.BASELINE

        self.assertIsNone(baseline())
        with unittest.mock.patch.object(self.engine, "BASELINE", None), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.engine.main(["run", "search"]), 2)
        (office / "baseline.path").write_text(f"{OFFICE}\n", encoding="utf-8")
        self.assertEqual(baseline(), OFFICE)
        for text in ("clone\n", "\n"):  # would follow the working folder, which for a worker is its writable task folder
            (office / "baseline.path").write_text(text, encoding="utf-8")
            with self.assertRaises(SystemExit):
                baseline()

    def test_preflight_runs_the_baseline_script_in_repo_for_pr_txt(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.engine.preflight("search"), 2)  # no pr.txt yet
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/1\n")
        done = lambda code, out="": unittest.mock.Mock(returncode=code, stdout=out, stderr="")  # noqa: E731
        head = "a" * 40
        fields = {"pr": 1, "fork": False, "head": head, "base": "refs/remotes/origin/main", "worktree": "same",
                  "unreviewed_examples": [], "secret_hits": [], "diff_argv": ["git", "-C", "/x", "diff"]}
        printed = json.dumps(fields) + "\n"
        quiet = contextlib.redirect_stdout(io.StringIO())
        with unittest.mock.patch.object(self.engine.subprocess, "run") as run, quiet:
            run.side_effect = [done(0, printed), done(0, b"diff --git a/x b/x\n")]
            self.assertEqual(self.engine.preflight("search"), 0)
            (script, kwargs), (diff, _) = run.call_args_list
            self.assertEqual((self.job / "pr.diff").read_bytes(), b"diff --git a/x b/x\n")
            run.side_effect = [done(1, printed)]  # the preflight's "secrets file changed, stop the review"
            self.assertEqual(self.engine.preflight("search"), 1)
            self.assertFalse((self.job / "pr.diff").exists())  # exit 1 removes the last diff and writes none
            # A non-UTF-8 file in the PR must not crash: an uncaught error would exit 1 like a secrets file.
            run.side_effect = [done(0, printed), done(0, b"caf\xe9\n")]
            self.assertEqual(self.engine.preflight("search"), 0)
            self.assertEqual((self.job / "pr.diff").read_bytes(), b"caf\xe9\n")
        self.assertEqual(script[0][1:], [str(self.engine.BASELINE / "tools/review_preflight.py"), "https://github.com/o/r/pull/1", "--json"])
        self.assertEqual(kwargs["cwd"], self.job / "repo")
        # The worker cannot run the printed command (absolute -C path, quoted pathspecs), so the engine builds the same diff.
        self.assertEqual(diff[0], ["git", "-C", f"{self.job}/repo", "diff", "--ignore-submodules=none", f"refs/remotes/origin/main...{head}", "--", ".", ":!.env*", ":!**/.env*"])
        # Text output, a secrets hit with exit 0, a missing diff_argv, or extra git options in head or base never reach git.
        bad = [{**fields, "head": head + " --output=/tmp/x"}, {**fields, "base": "--output=/tmp/x"},
               {**fields, "secret_hits": [".env"]}, {**fields, "diff_argv": None}, {"head": head}, [head]]
        text = f"head: {head}\ndiff: git -C / diff refs/remotes/origin/main...{head} -- . ':!.env*' ':!**/.env*'\n"
        for out in [text, printed + "x\n"] + [json.dumps(b) for b in bad]:
            with unittest.mock.patch.object(self.engine.subprocess, "run") as run, quiet, contextlib.redirect_stderr(io.StringIO()):
                run.side_effect = [done(0, out)]
                self.assertEqual(self.engine.preflight("search"), 2)
                self.assertEqual(run.call_count, 1)

    def test_skills_runs_check_skills_in_repo_with_the_preflight_base_only(self):
        with unittest.mock.patch.object(self.engine.subprocess, "run") as run:
            run.return_value = unittest.mock.Mock(returncode=2)
            self.assertEqual(self.engine.skills("search", "refs/remotes/origin/main"), 2)
            (argv, kwargs), = run.call_args_list
            with contextlib.redirect_stderr(io.StringIO()):
                for base in ("--output=/tmp/x", "main", "refs/remotes/origin/main --x", "refs/remotes/origin/main..HEAD",
                             "refs/remotes/origin/main...x", "refs/remotes/../../x"):
                    self.assertEqual(self.engine.skills("search", base), 2)
        self.assertEqual(run.call_count, 1)  # a bad base never runs the script
        script = str(self.engine.BASELINE / "profiles/matt-skills/check_skills.py")
        self.assertEqual(argv[0][1:], ["-I", script, "refs/remotes/origin/main", "--harness", "claude",
                                       "--settings", str(self.engine.OFFICE / ".claude/settings.json")])
        self.assertEqual(kwargs["cwd"], self.job / "repo")

    @unittest.skipUnless(os.environ.get("OFFICE_LIVE_CLAUDE") == "1" and shutil.which("claude"), "set OFFICE_LIVE_CLAUDE=1 to start a real worker")
    def test_a_real_worker_may_develop_but_never_push(self):
        # The docs say deny rules block in every permission mode (permission-modes, 2026-11-14); this checks the worker's argv.
        bare = Path(self.tmp.name) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        repo = self.job / "repo"
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
        config = json.loads(self.engine.STAGES.read_text())
        stage = next(s for s in config["flow"] if s["id"] == "implement")
        argv = self.engine.claude_argv(config, stage, self.job, json.loads((self.job / "task.json").read_text()),
                                       {"sessions": []})
        prompt = ("Run these Bash commands, one call each, exactly as written, then reply done: "
                  "(1) touch repo/made  (2) git -C repo push origin HEAD:refs/heads/x  (3) git push --help >/dev/null; "
                  "cd repo && git push origin HEAD:refs/heads/y")
        subprocess.run([argv[0], "-p", prompt, "--model", "haiku", *argv[1:]], cwd=self.job, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=600)
        self.assertTrue((repo / "made").exists())  # a normal command ran
        heads = subprocess.run(["git", "-C", str(bare), "for-each-ref", "refs/heads"], capture_output=True, text=True).stdout
        self.assertEqual(heads, "")  # neither push reached the remote

    def product(self, setup="N/A: nothing", check="N/A: no linter", test="true", template=True):
        """A product repository with an AGENTS.md 「명령」 section and a GitHub origin backed by a local bare one."""
        repo, bare = Path(self.tmp.name) / "product", Path(self.tmp.name) / "product.git"
        repo.mkdir()
        (repo / "AGENTS.md").write_text(f"# p\n\n## 명령\n\n- setup: `{setup}` (why)\n- check: `{check}`\n- test: `{test}`\n"
                                        "- build: `N/A: none`\n\n## 길잡이\n\n- test: `not this one`\n", encoding="utf-8")
        if template:
            (repo / ".github").mkdir()
            (repo / ".github/pull_request_template.md").write_text("## Summary\n", encoding="utf-8")
        (repo / ".gitignore").write_text("setup-ran\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        for args in (["init", "-q", "-b", "main"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "p"],
                     ["remote", "add", "origin", "https://github.com/o/p.git"], ["config", f"url.{bare}.insteadOf", "https://github.com/o/p.git"],
                     ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        return repo

    def test_command_cells_come_from_the_agents_md_section_and_n_a_drops(self):
        text = ("## 명령\n\n- setup: `N/A: none`\n- check: `make lint` (note `x`)\n- test: `make test`\n\n"
                "## 길잡이\n- test: `other`\n")
        self.assertEqual(self.engine.commands(text), {"setup": None, "check": "make lint", "test": "make test"})
        self.assertIn("no `## 명령` section", self.engine.commands("# x\n"))
        self.assertIn("- check:", self.engine.commands("## 명령\n- setup: `a`\n- test: `b`\n"))
        self.assertIn("test cell is N/A", self.engine.commands("## 명령\n- setup: `a`\n- check: `b`\n- test: `N/A: x`\n"))

    def test_new_takes_check_and_test_from_the_product_and_runs_setup_once(self):
        repo = self.product(setup="echo s >> setup-ran", check="make lint", test="make test")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.new("connect", str(repo), "origin/main"), 0)
        job = self.engine.JOBS / "connect"
        task = json.loads((job / "task.json").read_text())
        self.assertEqual((task["test"], task["setup"]), ("(\nmake lint\n) && (\nmake test\n)", "echo s >> setup-ran"))
        self.assertEqual((job / "repo/setup-ran").read_text(), "s\n")  # in repo/, once
        self.assertFalse((job / "BLOCKED.md").exists())

    def test_a_failing_check_cell_fails_the_task_test_even_if_the_test_cell_has_a_semicolon(self):
        repo = self.product(check="false", test="true; true")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.new("connect", str(repo), "main"), 0)
            self.assertNotEqual(self.engine.test("connect"), 0)

    def test_a_comment_in_a_cell_does_not_break_the_task_test(self):
        repo = self.product(check="true # a note", test="true")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.new("connect", str(repo), "main"), 0)
            self.assertEqual(self.engine.test("connect"), 0)

    def test_new_refuses_a_product_without_command_cells(self):
        repo = self.product()
        (repo / "AGENTS.md").write_text("# p\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "x"], check=True)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.engine.new("connect", str(repo), "main"), 2)
        self.assertIn("engine.py doctor", err.getvalue())
        self.assertFalse((self.engine.JOBS / "connect").exists())

    def test_a_failing_setup_blocks_the_new_task(self):
        repo = self.product(setup="echo broken; exit 3")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.new("connect", str(repo), "main"), self.engine.BLOCKED)
        blocked = (self.engine.JOBS / "connect/BLOCKED.md").read_text()
        self.assertIn("exit 3", blocked)
        self.assertIn("broken", blocked)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("connect", FakeWorker(self.engine.JOBS / "connect")), self.engine.BLOCKED)

    def contract_tool(self, code, out="", err=""):
        """repo-baseline's apply_github_settings.py in the fake clone: prints out and err, exits code, records its argv."""
        argv = Path(self.tmp.name) / "contract-argv"
        (self.baseline / "tools/apply_github_settings.py").write_text(
            f"import json, sys\nopen({str(argv)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
            f"print({out!r})\nprint({err!r}, file=sys.stderr)\nsys.exit({code})\n", encoding="utf-8")
        return lambda: [json.loads(l) for l in argv.read_text().splitlines()] if argv.exists() else []  # noqa: E731

    def test_doctor_checks_the_baseline_contract_by_its_plan_only(self):
        self.fake_gh()
        repo = self.product()
        ran = self.contract_tool(0, "BASELINE: x\nNo writes needed")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 0, out.getvalue())
        self.assertIn("ok   GitHub settings match the repo-baseline contract", out.getvalue())
        self.contract_tool(1, 'BASELINE: x\nDIFF: rulesets.main.strict: true -> false\nPLAN: PUT repos/o/p/rulesets/1 {"long": 1}\n'
                              "MANUAL: protection carries signed commits\nPlan only; nothing written.")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 1)
        failed = [l for l in out.getvalue().splitlines() if l.startswith("FAIL")]
        self.assertEqual(len(failed), 1, out.getvalue())
        self.assertIn("GitHub settings match the repo-baseline contract", failed[0])
        self.assertIn("DIFF: rulesets.main.strict: true -> false", out.getvalue())
        self.assertIn("MANUAL: protection carries signed commits", out.getvalue())
        self.assertNotIn("PLAN: PUT", out.getvalue())  # the fix shows what differs, not the long write bodies
        # A plan that could not read the server is unverified, never a pass.
        self.contract_tool(2, err="FAILED: HTTP 403: Resource not accessible")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 1)
        self.assertIn("확인 못 함 (unverified): FAILED: HTTP 403", out.getvalue())
        self.contract_tool(1, err="ModuleNotFoundError: No module named 'check_github_settings'")  # a crash exits 1 too
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 1)
        self.assertIn("확인 못 함 (unverified): ModuleNotFoundError", out.getvalue())
        self.assertNotIn("the owner applies it", out.getvalue())
        (self.baseline / "tools/apply_github_settings.py").unlink()  # a clone without the tool: also unverified
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 1)
        self.assertIn("확인 못 함", out.getvalue())
        # The tool read a fresh checkout of origin/main, one argument and no --apply, every time; the checkout is gone.
        self.assertEqual([len(a) for a in ran()], [1, 1, 1, 1])
        self.assertTrue(all(a[0].endswith("/main") and not Path(a[0]).exists() for a in ran()), ran())

    def test_doctor_contract_reads_main_and_refuses_another_repositorys_settings(self):
        self.fake_gh()
        repo = self.product()
        ran = self.contract_tool(0, "No writes needed")
        git = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True)  # noqa: E731
        settings = repo / ".github/repository-settings.json"
        settings.write_text('{"repository": "o/other"}\n', encoding="utf-8")
        git("add", "."), git("commit", "-qm", "copied settings"), git("push", "-q", "origin", "main")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 1)
        failed = [l for l in out.getvalue().splitlines() if l.startswith("FAIL")]
        self.assertEqual(len(failed), 1, out.getvalue())
        self.assertIn("names 'o/other', but origin is o/p", failed[0])
        self.assertEqual(ran(), [])  # a pass of the tool would be for o/other: it is not asked
        settings.write_text('{"repository": "o/p"}\n', encoding="utf-8")  # fixed only in the working tree: main still names o/other
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 1)
        self.assertIn("names 'o/other'", out.getvalue())
        settings.write_text('{"repository": "O/P"}\n', encoding="utf-8")  # owner and name are case-insensitive
        git("commit", "-qam", "own settings"), git("push", "-q", "origin", "main")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 0, out.getvalue())
        self.assertEqual(len(ran()), 1)

    def test_doctor_says_what_the_product_lacks(self):
        _, calls = self.fake_gh()  # gh answers 0: logged in, main protected
        self.contract_tool(0)  # the product follows the baseline contract
        repo = self.product(setup="echo s >> setup-ran", test="test -f setup-ran")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 0, out.getvalue())
        self.assertNotIn("FAIL", out.getvalue())
        self.assertIn(["api", "repos/o/p/branches/main/protection"], calls())
        self.assertFalse((repo / "setup-ran").exists())  # it ran in a fresh checkout of main, not here
        # A second product with a failing test and no template.
        shutil.rmtree(repo)
        shutil.rmtree(Path(self.tmp.name) / "product.git")
        repo = self.product(test="exit 5", template=False)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.engine.doctor(str(repo)), 1)
        failed = [l for l in out.getvalue().splitlines() if l.startswith("FAIL")]
        self.assertEqual(len(failed), 2, out.getvalue())
        self.assertIn("pull_request_template.md", failed[0])
        self.assertIn("test passes on main: `exit 5`: exit 5", failed[1])
        self.assertEqual(subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True).stdout.count("\n"), 1)

    def routed(self, design_text, fail_haiku=None, fail_model="haiku"):
        """Run to the draft wait with a design that says design_text; fail_haiku: "silent", "blocked" or "check" for the first
        implement on fail_model. self.notes gets the board note each later implement run sees as it starts."""
        job, failed, engine, self.notes = self.job, [], self.engine, []

        class Worker(FakeWorker):
            def __call__(self, name, cwd, argv, message):
                stage = name.rsplit("-", 1)[1]
                model = argv[argv.index("--model") + 1]
                fails = stage == "implement" and model == fail_model and fail_haiku and not failed
                if fails:
                    failed.append(model)
                elif stage == "implement" and failed:
                    test_notes.append(engine.job_status(job, ask=False)["note"])
                if fails and fail_haiku == "check":
                    result = super().__call__(name, cwd, argv, message)
                    (job / "repo/pass").unlink()  # writes its result but leaves a commit whose test fails
                    return result
                if fails:
                    self.calls.append((stage, argv, message))
                    if fail_haiku == "blocked":
                        (job / "BLOCKED.md").write_text("gave up\n", encoding="utf-8")
                    return None
                result = super().__call__(name, cwd, argv, message)
                if stage == "design":
                    (job / "design.md").write_text(design_text, encoding="utf-8")
                if stage == "implement":
                    (job / "repo/pass").write_text("")
                return result

        test_notes = self.notes
        worker = Worker(job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", worker)
        return [(c[0], c[1][c[1].index("--model") + 1], c[1][c[1].index("--resume" if "--resume" in c[1] else "--session-id") + 1])
                for c in worker.calls]

    def test_each_stage_gets_its_model_from_the_approved_difficulty(self):
        work = self.engine.session_id("search", "work")
        self.assertEqual(self.routed("# d\n\n난이도: 쉬움\n"), [("design", "opus", work), ("implement", "haiku", work)])
        self.assertEqual(self.runs(), [{"stage": "design", "model": "opus", "effort": "medium", "rerun": False},
                                   {"stage": "implement", "model": "haiku", "effort": "medium", "rerun": False}])
        for text in ("# d\n", "난이도: 보통\n", "난이도: 어려움\n", "난이도: easy\n", "난이도: 쉬움\n난이도: 보통\n", "- 난이도: 쉬움\n"):
            with self.subTest(text=text):
                shutil.rmtree(self.job / "repo")
                for f in self.job.iterdir():
                    if f.is_file() and f.name not in ("task.json", "INTENT.md"):
                        f.unlink()
                subprocess.run(["git", "init", "-q", str(self.job / "repo")], check=True)
                for args in (["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                    subprocess.run(["git", "-C", str(self.job / "repo"), *args], check=True)
                (self.job / "repo/.git/info/exclude").write_text("pass\n")
                (self.job / "repo/pass").write_text("")
                self.assertEqual([m for _, m, _ in self.routed(text)], ["opus", "sonnet"])

    def test_the_review_runs_on_opus_unless_the_pr_proved_easy(self):
        stage = next(s for s in json.loads(self.engine.STAGES.read_text())["flow"] if s["id"] == "review")
        flow = json.loads(self.engine.STAGES.read_text())["flow"]
        self.assertEqual(self.engine.stage_model(stage, self.job, {}, flow), "opus")
        self.assertEqual(self.engine.stage_model(stage, self.job, {"easy_pr": "abc", "checked": "abc"}, flow), "sonnet")
        self.assertEqual(self.engine.stage_model(stage, self.job, {"easy_pr": "abc", "checked": "def"}, flow), "opus")  # a later commit
        self.assertNotIn("haiku", [self.engine.stage_model(stage, self.job, st, flow) for st in ({}, {"easy_pr": "a", "checked": "a"})])

    def test_the_worker_gets_the_effort_its_stage_sets_for_its_model(self):
        config, task = json.loads(self.engine.STAGES.read_text()), json.loads((self.job / "task.json").read_text())
        stage = {"id": "review", "session": "review", "model": "door", "effort": {"opus": "high", "sonnet": "medium"}}
        for state, want in (({}, "high"), ({"easy_pr": "a", "checked": "a"}, "medium")):
            argv = self.engine.claude_argv(config, stage, self.job, task, {"sessions": [], **state})
            self.assertEqual(argv[argv.index("--effort") + 1], want)
        argv = self.engine.claude_argv(config, {**stage, "effort": {"opus": "high"}}, self.job, task,
                                       {"sessions": [], "easy_pr": "a", "checked": "a"})
        self.assertNotIn("--effort", argv)  # no entry for sonnet: the model's own default

    def test_the_stage_table_sets_a_valid_effort_for_every_model_a_worker_stage_can_run(self):
        routes = {"difficulty": {"haiku", "sonnet"}, "door": {"opus", "sonnet"}}
        for stage in (s for s in json.loads(self.engine.STAGES.read_text())["flow"] if s["by"] == "claude"):
            effort = stage.get("effort", {})
            self.assertEqual(set(effort), routes.get(stage["model"], {stage["model"]}), stage["id"])
            self.assertLessEqual(set(effort.values()), {"low", "medium", "high", "xhigh", "max"}, stage["id"])

    def test_a_failed_haiku_implement_reruns_once_on_sonnet_in_a_new_session(self):
        work, fresh = self.engine.session_id("search", "work"), self.engine.session_id("search", "work-sonnet")
        for mode in ("silent", "check", "blocked"):
            with self.subTest(mode=mode):
                if mode != "silent":
                    self.setUp()
                calls = self.routed("난이도: 쉬움\n", fail_haiku=mode)
                self.assertEqual(calls, [("design", "opus", work), ("implement", "haiku", work), ("implement", "sonnet", fresh)])
                self.assertEqual(self.runs()[-1], {"stage": "implement", "model": "sonnet", "effort": "medium", "rerun": True})
                self.assertIn("exit 0", (self.job / "check.md").read_text())  # the sonnet run passed the check
                self.assertNotIn("gave up", (self.job / "BLOCKED.md").read_text() if (self.job / "BLOCKED.md").exists() else "")
                # The haiku try that failed is kept as a failure, never as a block.
                self.assertEqual([(f["model"], f.get("blocked")) for f in self.state()["failures"]][:1], [("haiku", None)])
                if mode == "blocked":  # the engine flow itself: sonnet starts with since unchanged, so the board says why
                    self.assertEqual(self.notes, ["직전 실패: gave up"])
        self.assertTrue((self.job / "BLOCKED.haiku.md").exists())
        # While sonnet reruns the stage haiku gave up on, the board says why; once the stage moves on, it does not.
        (self.job / "pr.txt").unlink(missing_ok=True)
        (self.job / "BLOCKED.md").unlink(missing_ok=True)  # a block's own reason comes first
        self.assertEqual(self.engine.job_status(self.job, ask=False)["note"], "")
        gave_up = self.state()["failures"][-1]["at"]
        self.engine.save(self.job / "state.json", {**self.state(), "stage": "implement", "fails": {}, "since": gave_up - 1.5})
        status = self.engine.job_status(self.job, ask=False)
        self.assertIn("직전 실패", status["note"])
        newest = max((f for f in self.job.iterdir() if f.is_file() and f.name != "run.lock"), key=lambda f: f.stat().st_mtime)
        self.assertTrue(status["last"].startswith(newest.name), status["last"])  # the note keeps the newest file's name
        # Back in implement later (a block answered, a check that sends it back): the old give-up is not why it runs.
        self.engine.save(self.job / "state.json", {**self.state(), "since": gave_up + 1.0})
        self.assertEqual(self.engine.job_status(self.job, ask=False)["note"], "")
        self.engine.save(self.job / "state.json", {**self.state(), "since": gave_up - 1.5})
        # A haiku run that stopped on a block (hc error, usage limit) is not a failed try: no note for it.
        *earlier, last = self.state()["failures"]
        self.engine.save(self.job / "state.json", {**self.state(), "failures": [*earlier, {**last, "blocked": True}]})
        self.assertEqual(self.engine.job_status(self.job, ask=False)["note"], "")

    def test_the_failure_note_without_fails_is_only_the_haiku_give_up_being_rerun(self):
        since = 1000.6
        self.engine.save(self.job / "state.json", {"stage": "implement", "since": since})
        (self.job / "BLOCKED.md").unlink(missing_ok=True)
        (self.job / "pr.txt").unlink(missing_ok=True)

        def note(fails=None, **fail):
            self.engine.save(self.job / "state.json", {"stage": "implement", "since": since, "fails": fails or {},
                                                       "failures": [{"stage": "implement", "reason": "why", "model": "haiku",
                                                                     "at": 1003, **fail}]})
            return self.engine.job_status(self.job, ask=False)["note"]
        self.assertEqual(note(), "직전 실패: why")
        # Twice failed then answered: fails is cleared twice and since is kept, but a sonnet failure is no give-up.
        self.assertEqual(note(model="sonnet"), "")
        self.assertEqual(note(at=1000), "직전 실패: why")  # the same second as since: at is whole seconds
        old = note(); fail = self.state()["failures"][0]; del fail["at"]
        self.engine.save(self.job / "state.json", {**self.state(), "failures": [fail]})
        self.assertEqual((old, self.engine.job_status(self.job, ask=False)["note"]), ("직전 실패: why", ""))  # no at: hidden
        # Another stage gave up: record() runs before move(), so only a sonnet pass in the give-up's second gets here.
        self.engine.save(self.job / "state.json", {"stage": "check", "since": since, "fails": {}, "failures": [
            {"stage": "implement", "reason": "why", "model": "haiku", "at": 1000}]})
        self.assertEqual(self.engine.job_status(self.job, ask=False)["note"], "")
        self.assertEqual(note(fails={"implement": 1}, model="sonnet"), "직전 실패: why")  # fails shows any failure

    def test_a_transient_error_is_retried_once_then_blocks(self):
        self.fake_github()
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        answers = ["gh pr ready failed: timeout", None]
        with unittest.mock.patch.object(self.engine, "make_ready", side_effect=lambda *a: answers.pop(0)):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.engine.run("search", worker)
        self.assertIn("retrying once", out.getvalue())
        self.assertEqual(self.state()["stage"], "review")  # the second try passed
        self.engine.save(self.job / "state.json", {**self.state(), "stage": "ready", "retried": {}})
        (self.job / "BLOCKED.md").unlink(missing_ok=True)
        with unittest.mock.patch.object(self.engine, "make_ready", return_value="gh pr ready failed: down") as made:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
        self.assertEqual(made.call_count, 2)
        self.assertIn("gh pr ready failed", (self.job / "BLOCKED.md").read_text())

    def test_a_block_answered_in_intent_runs_on_and_the_worker_rereads_it(self):
        class Worker(FakeWorker):
            def __call__(self, name, cwd, argv, message):
                if name.endswith("design") and len(self.calls) == 0:
                    self.calls.append(("design", argv, message))
                    (self.job / "BLOCKED.md").write_text("which API?\n", encoding="utf-8")
                    return None
                return super().__call__(name, cwd, argv, message)

        worker = Worker(self.job)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
            self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)  # no answer yet
        intent = self.job / "INTENT.md"
        intent.write_text(intent.read_text() + "- 2026-11-14: v2 API를 쓴다\n", encoding="utf-8")
        os.utime(intent, (time.time() + 5, time.time() + 5))
        stamp = int((self.job / "BLOCKED.md").stat().st_mtime)
        earlier = self.job / f"BLOCKED.{stamp}.md"  # a note set aside earlier in the same second
        earlier.write_text("earlier\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)  # design written, waits for approval
        self.assertEqual(len(worker.calls), 2)
        self.assertIn("Re-read", worker.calls[1][2])
        self.assertIn(str(intent), worker.calls[1][2])
        self.assertFalse((self.job / "BLOCKED.md").exists())
        self.assertEqual(earlier.read_text(encoding="utf-8"), "earlier\n")  # the old block is kept beside it
        self.assertEqual((self.job / f"BLOCKED.{stamp}.1.md").read_text(encoding="utf-8"), "which API?\n")

    def test_a_worker_that_blocks_and_writes_intent_does_not_unblock_itself(self):
        class Worker(FakeWorker):
            def __call__(self, name, cwd, argv, message):
                self.calls.append(("design", argv, message))
                if len(self.calls) > 3:
                    raise AssertionError("the engine kept rerunning a stage its worker blocked")
                (self.job / "BLOCKED.md").write_text("which API?\n", encoding="utf-8")
                intent = self.job / "INTENT.md"
                intent.write_text(intent.read_text() + "- 질문: which API?\n", encoding="utf-8")
                os.utime(intent, (time.time() + 5, time.time() + 5))
                return None

        worker = Worker(self.job)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
        self.assertEqual(len(worker.calls), 1)

    def test_a_task_saved_at_the_old_pr_stage_goes_on_from_ready(self):
        draft, gh = self.fake_gh()
        (self.job / "pr.txt").write_text("https://github.com/O/R/pull/7\n", encoding="utf-8")
        self.engine.save(self.job / "state.json", {"stage": "pr", "since": 0, "fails": {}, "sessions": []})
        with unittest.mock.patch.object(self.engine, "unpinned", return_value="stop at the review"):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.engine.run("search", FakeWorker(self.job)), self.engine.BLOCKED)
        self.assertEqual(self.state()["stage"], "review")
        self.assertIn(["pr", "ready"], [c[:2] for c in gh()])

    def test_new_fetches_the_base_and_blocks_while_setup_has_not_passed(self):
        repo = self.product(setup="echo first", test="true")
        # origin moved after this checkout fetched: the new setup must come from origin, not the stale local ref.
        other = Path(self.tmp.name) / "other"
        # -b main: the bare origin's HEAD follows git's default branch name, which is not main everywhere (CI)
        subprocess.run(["git", "clone", "-q", "-b", "main", str(Path(self.tmp.name) / "product.git"), str(other)], check=True, capture_output=True)
        jobs = self.engine.JOBS
        (other / "AGENTS.md").write_text("## 명령\n\n- setup: `test -f ../BLOCKED.md && echo held > held`\n- check: `N/A: x`\n"
                                         "- test: `true`\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "moved"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.new("connect", str(repo), "origin/main"), 0)
        job = jobs / "connect"
        self.assertEqual(json.loads((job / "task.json").read_text())["setup"], "test -f ../BLOCKED.md && echo held > held")
        self.assertTrue((job / "repo/held").exists())  # BLOCKED.md stood while setup ran
        self.assertFalse((job / "BLOCKED.md").exists())  # and went once it passed

    def test_an_answered_setup_block_runs_setup_again_first(self):
        repo = self.product(setup="test -f ../fixed")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.new("connect", str(repo), "main"), self.engine.BLOCKED)
        job = self.engine.JOBS / "connect"
        worker = FakeWorker(job)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("connect", worker), self.engine.BLOCKED)
        (job / "fixed").write_text("")
        os.utime(job / "INTENT.md", (time.time() + 5, time.time() + 5))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.run("connect", worker), self.engine.WAIT)
        self.assertIn("exit 0", (job / "setup.md").read_text())
        self.assertEqual([c[0] for c in worker.calls], ["design"])

    def test_workers_reads_files_only(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", FakeWorker(self.job))
        (self.job / "BLOCKED.md").write_text("# 막힘\n\nsetup failed: x\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.engine.workers(), 0)
        self.assertIn("blocked", out.getvalue())
        self.assertIn("opus", out.getvalue())  # the design ran last
        self.assertIn("setup failed: x", out.getvalue())


    def board_data(self, page):
        """The JSON the page draws from: the only place worker text goes into the page."""
        start = page.index('<script type="application/json" id="data">') + len('<script type="application/json" id="data">')
        return json.loads(page[start:page.index("</script>", start)])

    # The page's own script, run in node against a minimal DOM: what it puts on screen, not how its source looks.
    PAGE_HARNESS = r"""
const {data, script} = JSON.parse(require("fs").readFileSync(0, "utf8"));
const made = [];
function El(tag) {
  const e = {tag, text: "", href: "", className: "", title: "", style: {setProperty() {}},
             append(...c) {}, set innerHTML(v) { made.push({tag: "innerHTML", text: String(v), href: ""}); }};
  Object.defineProperty(e, "textContent", {get() { return e.text; }, set(v) { e.text = String(v); }});
  made.push(e);
  return e;
}
const nodes = {data: {textContent: data}, meta: El("span"), lanes: El("section"), cards: El("section")};
globalThis.document = {getElementById: id => nodes[id], createElement: El};
globalThis.window = {parent: {postMessage(m) { made.push({tag: "posted", text: JSON.stringify(m), href: ""}); }}};
globalThis.location = {reload() {}};
globalThis.setTimeout = () => 0;
new Function(script)();
console.log(JSON.stringify(made.map(e => ({tag: e.tag, text: e.text, href: e.href}))));
"""

    def run_page(self, age=0):
        """Every element the page script made, as {tag, text, href}; age makes the data that many seconds old."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        self.engine.board()
        page = (self.engine.JOBS / "board-tasks.html").read_text(encoding="utf-8")
        start = page.index('id="data">') + len('id="data">')
        data = page[start:page.index("</script>", start)]
        if age:
            data = json.dumps(dict(json.loads(data), stamp=json.loads(data)["stamp"] - age))
        body = page.index("<script>", start) + len("<script>")
        script = page[body:page.index("</script>", body)]
        ran = subprocess.run([node, "-e", self.PAGE_HARNESS], input=json.dumps({"data": data, "script": script}),
                             capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        return json.loads(ran.stdout)

    def test_board_shows_what_workers_wrote_as_text_only(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", FakeWorker(self.job))  # run rewrites the board
        self.assertIn("location.reload()", (self.engine.JOBS / "board-tasks.html").read_text(encoding="utf-8"))
        evil = '</script><img src=x onerror="alert(1)">&'
        (self.job / "BLOCKED.md").write_text(f"# 막힘\n\n{evil}\n", encoding="utf-8")
        made = self.run_page()
        self.assertIn(evil, [e["text"] for e in made])  # shown as the very text, through textContent
        self.assertFalse([e for e in made if e["tag"] == "innerHTML" and "onerror" in e["text"]])
        self.assertIn("막힘", [e["text"] for e in made])

    def test_a_failed_sonnet_implement_reruns_once_one_effort_level_higher(self):
        work = self.engine.session_id("search", "work")
        for mode in ("silent", "check"):
            with self.subTest(mode=mode):
                if mode != "silent":
                    self.setUp()
                self.assertEqual(self.routed("난이도: 보통\n", fail_haiku=mode, fail_model="sonnet"),
                                 [("design", "opus", work), ("implement", "sonnet", work), ("implement", "sonnet", work)])
                self.assertEqual([m.get("effort") for m in self.state()["models"]], ["medium", "medium", "high"])
                self.assertIn("exit 0", (self.job / "check.md").read_text())
        stage = {"id": "implement", "effort": {"sonnet": "max"}}
        self.assertEqual(self.engine.stage_effort(stage, "sonnet", {"raised": ["implement"]}), "max")  # no level above max
        self.assertIsNone(self.engine.stage_effort(stage, "haiku", {"raised": ["implement"]}))

    def test_the_board_shows_progress_only_while_an_engine_holds_the_task(self):
        self.engine.save(self.job / "state.json", {"stage": "implement", "since": 0, "models": [
            {"stage": "design", "model": "opus", "rerun": False}, {"stage": "implement", "model": "haiku", "rerun": False},
            {"stage": "implement", "model": "sonnet", "rerun": True}]})
        row = self.engine.tasks()[0]
        self.assertEqual((row["running"], row["ran"]), (0, "design=opus implement=haiku implement=sonnet+"))
        self.assertTrue(row["worker"].endswith("-search-implement"))
        texts = " ".join(e["text"] for e in self.run_page())
        self.assertIn("멈춤", texts)  # no engine: the stage is not moving, whatever state.json says
        self.assertIn("engine.py run search", texts)
        self.assertIn("구현 · haiku→sonnet", texts)
        with open(self.job / "run.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # what engine.py run holds
            self.assertEqual(self.engine.tasks()[0]["running"], 1)
            texts = " ".join(e["text"] for e in self.run_page())
            self.assertIn("진행 중", texts)
            self.assertIn("hc attach", texts)
            self.assertIn("멈춤", " ".join(e["text"] for e in self.run_page(age=60)))  # data no engine rewrote lately
        self.assertEqual(self.engine.tasks()[0]["running"], 0)

    def test_a_run_rewrites_the_board_while_a_worker_works(self):
        self.engine.BOARD_EVERY = 0.05
        seen = []

        def slow(name, cwd, argv, message):
            board = self.engine.JOBS / "board-tasks.html"
            time.sleep(0.2)  # the keeper writes the board within BOARD_EVERY even before any stage moves
            before = board.stat().st_mtime_ns
            time.sleep(0.3)
            seen.append(board.stat().st_mtime_ns > before)
            # The model of the worker now running, though models records it only when the worker ends.
            task = self.board_data(board.read_text(encoding="utf-8"))["tasks"][0]
            seen.append((task["at"], task["model"], task["running"]))
            (self.job / "design.md").write_text("d\n", encoding="utf-8")
            return None

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", slow)
        self.assertEqual(seen, [True, ("design", "opus", 1)])
        page = (self.engine.JOBS / "board-tasks.html").read_text(encoding="utf-8")
        self.assertEqual([t["running"] for t in self.board_data(page)["tasks"]], [0])  # the run ended: no one holds the lock

    def test_a_board_probe_does_not_turn_a_run_away(self):
        probe = open(self.job / "run.lock", "a")
        fcntl.flock(probe, fcntl.LOCK_SH)  # what job_status holds for a moment while it writes the board
        threading.Timer(0.03, probe.close).start()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertNotEqual(self.engine.run("search", FakeWorker(self.job)), 2)
        self.assertNotIn("is running", err.getvalue())

    def test_the_board_links_only_github_pull_requests(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", FakeWorker(self.job))
        pr = self.job / "pr.txt"
        for url, linked in [("https://github.com/O/R/pull/7", True), ("javascript:alert(1)", False),
                            ("https://github.com.evil.example/O/R/pull/7", False), ("https://github.com/O/R/pull/7x", False)]:
            with self.subTest(url=url):
                pr.write_text(url + "\n", encoding="utf-8")
                links = [e["href"] for e in self.run_page() if e["tag"] == "a"]
                self.assertEqual(links, [url] if linked else [])

    def test_a_malformed_task_shows_as_unreadable_and_the_others_stay(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", FakeWorker(self.job))
        for name, state in [("nan", '{"stage": {"toString": 1}, "since": 0, "models": [{"model": NaN}]}'),
                            ("text", '{"stage": "design", "since": "yesterday"}'), ("bare", '{"stage": "design"}'),
                            ("flag", '{"stage": true, "since": 0}')]:
            other = self.engine.JOBS / name
            other.mkdir()
            (other / "task.json").write_text("{}", encoding="utf-8")
            (other / "state.json").write_text(state, encoding="utf-8")
        self.engine.board()
        page = (self.engine.JOBS / "board-tasks.html").read_text(encoding="utf-8")
        start = page.index('id="data">') + len('id="data">')
        data = json.loads(page[start:page.index("</script>", start)], parse_constant=lambda c: self.fail(c))
        stages = {t["name"]: t["stage"] for t in data["tasks"]}
        self.assertEqual((stages["text"], stages["bare"]), ("unreadable", "unreadable"))
        self.assertNotEqual(stages["search"], "unreadable")
        for task in data["tasks"]:
            self.assertTrue(all(type(v) in (str, int) for v in task.values()), task)  # bool is an int: JSON true would slip through isinstance
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.engine.workers(), 0)  # the table view lists every task too
        self.assertTrue(all(name in out.getvalue() for name in ("nan", "text", "bare", "flag", "search")), out.getvalue())
        texts = [e["text"] for e in self.run_page()]
        self.assertIn("읽기 실패", texts)
        self.assertIn("search", texts)

    def test_the_office_loads_nothing_from_elsewhere(self):
        shell = self.engine.board().read_text(encoding="utf-8")
        self.assertIn('src="board-tasks.html"', shell)
        self.assertFalse(re.findall(r"(?:src|href)\s*=\s*[\"']?(?:https?:|//)|url\(|import\s|fetch\(", shell))
        start = shell.index("const ART = ") + len("const ART = ")
        art = json.loads(shell[start:shell.index(";  //", start)])
        self.assertEqual(sorted(art), sorted(p.stem for p in (OFFICE / "pipeline/office").glob("*.png")))
        self.assertIn("char_5", art)
        self.assertTrue(all(v.startswith("data:image/png;base64,") for v in art.values()))

    def test_the_office_gets_each_task_with_the_cards_status(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", FakeWorker(self.job))  # waits for design approval
        (self.job / "BLOCKED.md").write_text("# 막힘\n\nx\n", encoding="utf-8")
        posted = [json.loads(e["text"]) for e in self.run_page() if e["tag"] == "posted"]
        self.assertEqual(posted, [{"office": [{"name": "search", "lane": "approve", "stage": "설계 승인", "key": "bad",
                                               "text": "막힘", "model": "", "by": "human"}]}])

    def test_a_merged_pr_leaves_the_board_and_gh_is_asked_rarely(self):
        bin_dir, log = Path(self.tmp.name) / "mergedgh", Path(self.tmp.name) / "merged.log"
        bin_dir.mkdir()
        answer = Path(self.tmp.name) / "answer"
        answer.write_text('{"state": "OPEN", "mergeStateStatus": "BEHIND"}\n', encoding="utf-8")
        (bin_dir / "gh").write_text(f"#!/bin/sh\necho \"$@\" >> {log}\ncat {answer}\n", encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine.save(self.job / "state.json", {"stage": "done", "since": 0})
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/7\n", encoding="utf-8")
        asked = lambda: len(log.read_text().splitlines()) if log.exists() else 0  # noqa: E731
        self.assertEqual(self.engine.tasks()[0]["stage"], "done")  # open: still waiting for a person to merge
        self.assertEqual((self.engine.tasks()[0]["stage"], self.engine.tasks()[0]["merge"]), ("done", "BEHIND"))
        # The board rewrites every 5 seconds: gh is not asked each time. An open finished PR is asked twice a minute:
        # pr view, and one graphql read of the merge queue (queue_removed), which pr view cannot give.
        self.assertEqual(asked(), 2)
        texts = [e["text"] for e in self.run_page()]
        self.assertIn("병합 대기", texts)  # the merge queue merges a PR whose base moved on (POLICY §5)
        self.assertNotIn("브랜치 갱신 필요", texts)
        answer.write_text('{"state": "OPEN", "mergeStateStatus": "DIRTY"}\n', encoding="utf-8")
        os.utime(self.job / ".merge-asked", (0, 0))
        self.assertEqual(self.engine.tasks()[0]["merge"], "DIRTY")
        self.assertIn("충돌", [e["text"] for e in self.run_page()])
        answer.write_text('{"state": "MERGED", "mergeStateStatus": "UNKNOWN"}\n', encoding="utf-8")
        os.utime(self.job / ".merge-asked", (0, 0))
        row = self.engine.tasks()[0]
        self.assertEqual((row["stage"], row["merged"]), ("merged", 1))
        self.assertEqual(row["last"].split()[0], "pr.txt")  # the engine's own marks are not the task's last file
        self.engine.tasks()
        self.assertEqual(asked(), 5)  # a yes is kept; a merged PR is not asked about the queue
        made = self.run_page()
        texts = [e["text"] for e in made]
        self.assertNotIn("search", texts)
        self.assertTrue(any("병합 끝나 숨김 1" in t for t in texts), texts)
        self.assertEqual([json.loads(e["text"]) for e in made if e["tag"] == "posted"], [{"office": []}])

    def test_a_pr_that_leaves_the_merge_queue_unmerged_blocks_the_task_once(self):
        bin_dir, log = Path(self.tmp.name) / "queuegh", Path(self.tmp.name) / "queue.log"
        bin_dir.mkdir()
        queue = Path(self.tmp.name) / "queue.json"
        (bin_dir / "gh").write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:3] == ['api', 'graphql']:\n"
            f"    text = open({str(queue)!r}).read()\n"
            "    sys.exit(3) if text == 'fail' else print(text)\n"
            "else: print(json.dumps({'state': 'OPEN', 'mergeStateStatus': 'CLEAN'}))\n", encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine.save(self.job / "state.json", {"stage": "done", "since": 0})
        (self.job / "pr.txt").write_text("https://github.com/O/R/pull/7\n", encoding="utf-8")

        def ask(typename, reason="CI failed", in_queue=False, state="OPEN", id_="E1"):
            node = {"__typename": typename, **({"id": id_, "createdAt": "2026-11-16T10:00:00Z", "reason": reason}
                                               if typename.startswith("Removed") else {})}
            queue.write_text(json.dumps({"data": {"repository": {"pullRequest": {
                "state": state, "isInMergeQueue": in_queue, "timelineItems": {"nodes": [node]}}}}}), encoding="utf-8")
            (self.job / ".merge-asked").unlink(missing_ok=True)
            return self.engine.tasks()[0]

        blocked = self.job / "BLOCKED.md"
        for quiet in (lambda: ask("AddedToMergeQueueEvent", in_queue=True),  # waiting in the queue
                      lambda: ask("RemovedFromMergeQueueEvent", reason="merged"),  # GitHub logs a merge as a removal
                      lambda: ask("RemovedFromMergeQueueEvent", in_queue=True),  # removed, then put back
                      lambda: (queue.write_text("fail"), (self.job / ".merge-asked").unlink(missing_ok=True),
                               self.engine.tasks())):  # gh cannot answer: no alert
            quiet()
            self.assertFalse(blocked.exists())
        row = ask("RemovedFromMergeQueueEvent", reason="The merge queue CI failed")
        self.assertEqual(row["stage"], "blocked")
        text = blocked.read_text()
        self.assertIn("removed from merge queue at 2026-11-16T10:00:00Z: The merge queue CI failed https://github.com/O/R/pull/7", text)
        self.assertIn("removed from merge queue", row["note"])
        graphql = [c for c in map(json.loads, log.read_text().splitlines()) if c[:2] == ["api", "graphql"]][-1]
        self.assertEqual(list(zip(graphql[4::2], graphql[5::2])), [("-f", "o=o"), ("-f", "r=r"), ("-F", "n=7")])
        # The leader answers (the engine moves BLOCKED.md aside): the same removal does not block again, a new one does.
        blocked.rename(self.job / "BLOCKED.1.md")
        ask("RemovedFromMergeQueueEvent", reason="The merge queue CI failed")
        self.assertFalse(blocked.exists())
        ask("RemovedFromMergeQueueEvent", reason="conflict", id_="E2")
        self.assertIn(": conflict ", blocked.read_text())
        self.engine.tasks(ask=False)  # the stage loop's board rewrite never asks gh
        calls = log.read_text()
        self.assertNotIn("enqueue", calls.lower())
        self.assertNotIn("mutation", calls)
        self.assertFalse([c for c in map(json.loads, calls.splitlines()) if c[:2] == ["pr", "merge"]])

    def test_notes_set_aside_in_the_same_second_are_all_kept(self):
        blocked = self.job / "BLOCKED.md"
        notes = [f"# 막힘\n\n{self.engine.QUEUE_BLOCK}2026-11-17T0{i}:00:00Z: x https://github.com/o/r/pull/7\n" for i in range(2)]
        for note in notes:  # clear_queue_block twice, then advance's answered block: all from second 100
            blocked.write_text(note, encoding="utf-8")
            os.utime(blocked, (100.5, 100.5))
            self.assertTrue(self.engine.clear_queue_block(self.job))
        blocked.write_text("# 막힘\n\nwhich API?\n", encoding="utf-8")
        os.utime(blocked, (100, 100))
        self.engine.set_aside(blocked)
        self.assertFalse(blocked.exists())
        kept = {p.name: p.read_text(encoding="utf-8") for p in self.job.glob("BLOCKED.*.md")}
        self.assertEqual(kept, {"BLOCKED.100.md": notes[0], "BLOCKED.100.1.md": notes[1],
                                "BLOCKED.100.2.md": "# 막힘\n\nwhich API?\n"})

    def test_a_note_set_aside_at_the_same_moment_elsewhere_is_never_overwritten(self):
        blocked, link = self.job / "BLOCKED.md", os.link
        blocked.write_text("# 막힘\n\nmine\n", encoding="utf-8")
        os.utime(blocked, (200, 200))
        taken = []

        def racing(src, dst):  # another process (the board) takes the free name just before this one moves onto it,
            if not taken:      # and run blocks again with block(), which writes BLOCKED.md in place
                taken.append(dst)
                Path(dst).write_text("theirs\n", encoding="utf-8")
                blocked.write_text("# 막힘\n\nnew block\n", encoding="utf-8")
            link(src, dst)
        with unittest.mock.patch.object(self.engine.os, "link", racing):
            self.engine.set_aside(blocked)
        self.assertEqual({p.name: p.read_text(encoding="utf-8") for p in self.job.glob("BLOCKED.*.md")},
                         {"BLOCKED.200.md": "theirs\n", "BLOCKED.200.1.md": "# 막힘\n\nmine\n"})
        self.assertEqual(blocked.read_text(encoding="utf-8"), "# 막힘\n\nnew block\n")  # the new block still shows
        self.assertEqual([p.name for p in self.job.glob(".BLOCKED.*")], [])  # no held copy left behind
        self.engine.set_aside(self.job / "BLOCKED.gone.md")  # already set aside by another mover: nothing to do

    def test_a_note_that_cannot_be_archived_stays_the_block(self):
        blocked, link = self.job / "BLOCKED.md", os.link
        note = f"# 막힘\n\n{self.engine.QUEUE_BLOCK}2026-11-17T00:00:00Z: x https://github.com/o/r/pull/7\n"
        blocked.write_text(note, encoding="utf-8")

        def full(src, dst):  # the archive name cannot be made (disk full); putting the note back still works
            if Path(dst).name != "BLOCKED.md":
                raise OSError(28, "No space left on device")
            link(src, dst)
        with unittest.mock.patch.object(self.engine.os, "link", full):
            self.assertFalse(self.engine.clear_queue_block(self.job))  # not cleared: the next check tries again
        self.assertEqual(blocked.read_text(encoding="utf-8"), note)
        self.assertEqual([p.name for p in [*self.job.glob(".BLOCKED.*"), *self.job.glob("BLOCKED.*.md")]], [])

    def test_without_hard_links_a_note_is_still_set_aside(self):
        blocked = self.job / "BLOCKED.md"
        for code in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV):  # exFAT, some network mounts
            with self.subTest(errno=code):
                for p in self.job.glob("BLOCKED*.md"):
                    p.unlink()
                (self.job / "BLOCKED.200.md").write_text("theirs\n", encoding="utf-8")
                note = f"# 막힘\n\n{self.engine.QUEUE_BLOCK}x https://github.com/o/r/pull/7\n"
                blocked.write_text(note, encoding="utf-8")
                os.utime(blocked, (200, 200))

                def no_links(src, dst):  # a new block() lands while the note is held; then the link fails
                    blocked.write_text("# 막힘\n\nnew block\n", encoding="utf-8")
                    raise OSError(code, os.strerror(code))
                with unittest.mock.patch.object(self.engine.os, "link", no_links):
                    self.assertTrue(self.engine.clear_queue_block(self.job))
                self.assertEqual({p.name: p.read_text(encoding="utf-8") for p in self.job.glob("BLOCKED.*.md")},
                                 {"BLOCKED.200.md": "theirs\n", "BLOCKED.200.1.md": note})
                self.assertEqual(blocked.read_text(encoding="utf-8"), "# 막힘\n\nnew block\n")
                self.assertEqual([p.name for p in self.job.glob(".BLOCKED.*")], [])

    def test_without_hard_links_a_note_that_cannot_be_archived_goes_back_without_overwriting(self):
        blocked, rename = self.job / "BLOCKED.md", os.rename
        for new_block in (False, True):
            with self.subTest(new_block=new_block):
                for p in self.job.glob("BLOCKED*.md"):
                    p.unlink()
                blocked.write_text("# 막힘\n\nmine\n", encoding="utf-8")
                os.utime(blocked, (200, 200))
                failed = []

                def no_links(src, dst):
                    if new_block:
                        blocked.write_text("# 막힘\n\nnew block\n", encoding="utf-8")
                    raise OSError(1, "Operation not permitted")

                def once(src, dst):  # the first archive rename fails; the rest work
                    if Path(dst).name.startswith("BLOCKED.2") and not failed:
                        failed.append(dst)
                        raise OSError(5, "Input/output error")
                    rename(src, dst)
                with unittest.mock.patch.object(self.engine.os, "link", no_links), \
                        unittest.mock.patch.object(self.engine.os, "rename", once), self.assertRaises(OSError):
                    self.engine.set_aside(blocked)
                kept = {p.name: p.read_text(encoding="utf-8") for p in self.job.glob("BLOCKED*.md")}
                self.assertEqual(kept, {"BLOCKED.md": "# 막힘\n\nnew block\n", "BLOCKED.200.md": "# 막힘\n\nmine\n"}
                                 if new_block else {"BLOCKED.md": "# 막힘\n\nmine\n"})
                self.assertEqual([p.name for p in self.job.glob(".BLOCKED.*")], [])

    def test_a_queue_block_clears_once_the_pr_is_back_in_the_queue_or_merged(self):
        bin_dir = Path(self.tmp.name) / "unblockgh"
        bin_dir.mkdir()
        view, queue = Path(self.tmp.name) / "view.json", Path(self.tmp.name) / "unqueue.json"
        (bin_dir / "gh").write_text(
            "#!/usr/bin/env python3\nimport sys\n"
            f"print(open({str(queue)!r} if sys.argv[1:3] == ['api', 'graphql'] else {str(view)!r}).read())\n", encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine.save(self.job / "state.json", {"stage": "done", "since": 0})
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/7\n", encoding="utf-8")
        blocked = self.job / "BLOCKED.md"
        aside = lambda: sorted(p.name for p in self.job.glob("BLOCKED.*.md"))  # noqa: E731

        def ask(state="OPEN", in_queue=False, removed="E1"):
            view.write_text(json.dumps({"state": state, "mergeStateStatus": "CLEAN"}), encoding="utf-8")
            node = ({"__typename": "RemovedFromMergeQueueEvent", "id": removed, "createdAt": "2026-11-17T01:00:00Z",
                     "reason": "The merge queue CI failed"} if removed else {"__typename": "AddedToMergeQueueEvent"})
            queue.write_text(json.dumps({"data": {"repository": {"pullRequest": {
                "state": state, "isInMergeQueue": in_queue, "timelineItems": {"nodes": [node]}}}}}), encoding="utf-8")
            (self.job / ".merge-asked").unlink(missing_ok=True)
            return self.engine.tasks()[0]

        self.assertEqual(ask()["stage"], "blocked")
        row = ask(in_queue=True, removed=None)  # a person fixed it and put it back in the queue
        self.assertEqual(row["stage"], "done")
        self.assertFalse(blocked.exists())
        self.assertEqual(len(aside()), 1)  # moved aside like an answered block, not deleted
        self.assertEqual(ask(removed="E2")["stage"], "blocked")  # out again: a new removal blocks again
        # Two readers at once: one holding an older in-queue answer moves the newer E2 note aside. The next look restores it.
        os.utime(blocked, (2, 2))
        self.assertEqual(ask(in_queue=True, removed=None)["stage"], "done")
        self.assertEqual(ask(removed="E2")["stage"], "blocked")
        # An in-queue answer that moves nothing keeps the mark, so the same event never overwrites another block.
        note = blocked.read_text(encoding="utf-8")
        other = "# 막힘\n\nCI failed on abc: ci https://x\n"
        blocked.write_text(other, encoding="utf-8")
        ask(in_queue=True, removed=None)
        ask(removed="E2")
        self.assertEqual(blocked.read_text(encoding="utf-8"), other)
        blocked.write_text(note, encoding="utf-8")
        os.utime(blocked, (1, 1))  # another mtime than the first note, so the two kept notes do not share a name
        row = ask(state="MERGED", removed=None)
        self.assertEqual((row["stage"], row["merged"]), ("merged", 1))
        self.assertFalse(blocked.exists())
        self.assertEqual(len(aside()), 3)
        # A block that is not the queue note stays, even on a merged task: only a person's answer clears it.
        blocked.write_text("# 막힘\n\nCI failed on abc: ci https://x\n", encoding="utf-8")
        self.assertEqual(self.engine.tasks()[0]["stage"], "blocked")
        self.assertTrue(blocked.exists())
        # A task merged before this fix, with the queue note still standing: the next look clears it without asking gh.
        blocked.write_text("# 막힘\n\nremoved from merge queue at 2026-11-17T01:00:00Z: x https://github.com/o/r/pull/7\n",
                           encoding="utf-8")
        view.write_text("not json", encoding="utf-8")
        self.assertEqual(self.engine.tasks()[0]["stage"], "merged")

    def test_ci_state_puts_a_failure_before_a_running_check(self):
        run = lambda **c: {"__typename": "CheckRun", **c}  # noqa: E731
        self.assertEqual(self.engine.ci_state(None), "")
        self.assertEqual(self.engine.ci_state([]), "")
        self.assertEqual(self.engine.ci_state([run(status="COMPLETED", conclusion="SUCCESS"),
                                               {"__typename": "StatusContext", "state": "SUCCESS"}]), "pass")
        self.assertEqual(self.engine.ci_state([run(status="COMPLETED", conclusion="SKIPPED"), run(status="IN_PROGRESS")]), "pending")
        self.assertEqual(self.engine.ci_state([{"__typename": "StatusContext", "state": "PENDING"}]), "pending")
        self.assertEqual(self.engine.ci_state([run(status="QUEUED"), run(status="COMPLETED", conclusion="FAILURE")]), "fail")
        self.assertEqual(self.engine.ci_state([{"__typename": "StatusContext", "state": "ERROR"}, "x"]), "fail")

    def test_the_board_shows_a_prs_ci_while_it_reviews_and_waits(self):
        bin_dir = Path(self.tmp.name) / "cigh"
        bin_dir.mkdir()
        answer = Path(self.tmp.name) / "ci-answer"
        (bin_dir / "gh").write_text(f"#!/bin/sh\ncat {answer}\n", encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        running = '[{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}]'
        failed = '[{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]'
        ask = lambda merge, checks, stage="done": (  # noqa: E731
            answer.write_text(f'{{"state": "OPEN", "mergeStateStatus": "{merge}", "statusCheckRollup": {checks}}}\n',
                              encoding="utf-8"),
            self.engine.save(self.job / "state.json", {"stage": stage, "since": 0}),
            (self.job / ".merge-asked").unlink(missing_ok=True))
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/7\n", encoding="utf-8")
        ask("BLOCKED", running, "review")
        self.assertEqual(self.engine.tasks()[0]["ci"], "pending")  # asked from Ready on, not only once the review is done
        self.assertIn("PR의 CI: 진행 중 (1분마다 확인)", [e["text"] for e in self.run_page()])
        with open(self.job / "run.lock", "w") as lock:  # the engine runs the review: the office label shows the CI
            fcntl.flock(lock, fcntl.LOCK_EX)
            office = [json.loads(e["text"]) for e in self.run_page() if e["tag"] == "posted"][0]["office"]
        self.assertEqual((office[0]["key"], office[0]["text"]), ("work", "CI 진행 중"))
        ask("BEHIND", failed)
        self.assertIn("CI 실패", [e["text"] for e in self.run_page()])
        ask("BLOCKED", running)
        self.assertIn("CI 진행 중", [e["text"] for e in self.run_page()])  # not 「병합 조건 대기」
        ask("UNSTABLE", failed)
        self.assertIn("CI 실패", [e["text"] for e in self.run_page()])
        ask("BEHIND", running)
        self.assertIn("CI 진행 중", [e["text"] for e in self.run_page()])
        ask("BEHIND", "[]")  # the merge queue merges it; updating the branch would move the reviewed head
        texts = [e["text"] for e in self.run_page()]
        self.assertIn("병합 대기", texts)
        self.assertNotIn("브랜치 갱신 필요", texts)
        ask("DIRTY", failed)
        self.assertIn("충돌", [e["text"] for e in self.run_page()])
        answer.write_text('{"state": "MERGED", "mergeStateStatus": "UNKNOWN", "statusCheckRollup": []}\n', encoding="utf-8")
        self.engine.save(self.job / "state.json", {"stage": "review", "since": 0})
        (self.job / ".merge-asked").unlink()
        self.assertEqual(self.engine.tasks()[0]["merged"], 0)  # merged mid-review: the engine's stage still finishes

    def test_a_stage_move_never_waits_on_gh_and_the_keeper_asks(self):
        bin_dir, log = Path(self.tmp.name) / "slowgh", Path(self.tmp.name) / "slow.log"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n"
                                    "echo '{\"state\": \"OPEN\", \"mergeStateStatus\": \"BLOCKED\", \"statusCheckRollup\": "
                                    "[{\"__typename\": \"CheckRun\", \"status\": \"QUEUED\"}]}'\n", encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine.save(self.job / "state.json", {"stage": "review", "since": 0})
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/7\n", encoding="utf-8")
        self.engine.refresh_board(ask=False)
        self.assertFalse(log.exists())  # only the last answer, of which there is none yet
        self.engine.refresh_board()
        self.assertEqual(len(log.read_text().splitlines()), 1)
        os.utime(self.job / ".merge-asked", (0, 0))  # stale: a move still keeps to the kept answer
        self.engine.refresh_board(ask=False)
        self.assertEqual(len(log.read_text().splitlines()), 1)
        self.assertIn('"ci": "pending"', (self.engine.JOBS / "board-tasks.html").read_text(encoding="utf-8"))

    def test_a_runs_own_board_rewrites_never_ask_gh(self):
        calls = []  # each stage move and the last rewrite pass ask=False; only the keeper thread asks gh
        self.engine.BOARD_EVERY = 1000
        with unittest.mock.patch.object(self.engine, "refresh_board", lambda ask=True: calls.append(ask)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", FakeWorker(self.job))  # design, then waits for approval
        self.assertTrue(calls)
        self.assertEqual(set(calls), {False})

    def test_board_watch_keeps_the_board_current_with_no_engine_and_runs_once(self):
        self.engine.BOARD_EVERY = 0.01
        self.engine.save(self.job / "state.json", {"stage": "design", "since": 0})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.watch_board(rounds=1), 0)
        first = self.board_data((self.engine.JOBS / "board-tasks.html").read_text(encoding="utf-8"))
        self.engine.save(self.job / "state.json", {"stage": "approve", "since": 0})  # no engine moved it
        time.sleep(1.1)  # stamp is in whole seconds
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.watch_board(rounds=2), 0)
        data = self.board_data((self.engine.JOBS / "board-tasks.html").read_text(encoding="utf-8"))
        self.assertEqual(data["tasks"][0]["at"], "approve")
        self.assertGreater(data["stamp"], first["stamp"])
        with open(self.engine.JOBS / ".board-watch.lock", "a") as lock:  # a watcher is already running
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (self.engine.JOBS / "board-tasks.html").unlink()
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.engine.watch_board(rounds=1), 0)
            self.assertIn("already", err.getvalue())
            self.assertFalse((self.engine.JOBS / "board-tasks.html").exists())

    def test_a_board_that_cannot_be_written_leaves_the_run_exit_code(self):
        def broken():
            raise TypeError("since is not a number")  # e.g. another task's hand-edited state.json
        err = io.StringIO()
        with unittest.mock.patch.object(self.engine, "board", broken), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            self.assertEqual(self.engine.run("search", FakeWorker(self.job)), self.engine.WAIT)  # design approval
        self.assertIn("board not written: TypeError", err.getvalue())

    def test_a_submodule_pointer_under_a_trust_name_is_seen_even_when_git_hides_submodules(self):
        repo = Path(self.tmp.name) / "sub"
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
        repo.mkdir()
        run("init", "-q")
        run("config", "user.email", "t@t"), run("config", "user.name", "t")
        run("config", "diff.ignoreSubmodules", "all")
        (repo / "a.txt").write_text("a\n")
        run("add", "a.txt"), run("commit", "-qm", "base")
        base = run("rev-parse", "HEAD").stdout.strip()
        run("update-index", "--add", "--cacheinfo", f"160000,{base},.claude")
        run("commit", "-qm", "pointer")
        git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
        self.assertTrue(self.engine.touches_trust(git, base, "HEAD"))


    def _review_repo(self, review):
        repo = Path(self.tmp.name) / "review"
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
        repo.mkdir()
        run("init", "-q")
        run("config", "user.email", "t@t"), run("config", "user.name", "t")
        (repo / "a.txt").write_text("a\n")
        if review is not None:
            (repo / "REVIEW.md").write_text(review, encoding="utf-8")
        run("add", "-A"), run("commit", "-qm", "base")
        base = run("rev-parse", "HEAD").stdout.strip()
        git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
        return repo, run, base, git

    def test_a_path_the_product_review_lists_as_sensitive_is_a_trust_path(self):
        review = "# 리뷰\n\n## 민감한 파일\n\n`.github/workflows/**`, `scripts/godot.env`, 폴더 `.claude/`.\n\n## 보고\n\n`docs/x.md`\n"
        repo, run, base, git = self._review_repo(review)
        (repo / "scripts").mkdir()
        (repo / "scripts/godot.env").write_text("v\n")
        run("add", "-A"), run("commit", "-qm", "engine")
        self.assertTrue(self.engine.touches_trust(git, base, "HEAD"))

    def test_a_path_outside_the_sensitive_section_is_not_a_trust_path(self):
        review = "## 민감한 파일\n\n`scripts/godot.env`\n\n## 보고\n\n`docs/x.md`\n"
        repo, run, base, git = self._review_repo(review)
        (repo / "docs").mkdir()
        (repo / "docs/x.md").write_text("x\n")
        run("add", "-A"), run("commit", "-qm", "docs")
        self.assertFalse(self.engine.touches_trust(git, base, "HEAD"))

    def test_the_sensitive_list_is_read_at_base_so_the_branch_cannot_shorten_it(self):
        repo, run, base, git = self._review_repo("## 민감한 파일\n\n`scripts/godot.env`\n")
        (repo / "REVIEW.md").write_text("## 민감한 파일\n\n없음\n", encoding="utf-8")
        (repo / "scripts").mkdir()
        (repo / "scripts/godot.env").write_text("v\n")
        run("add", "-A"), run("commit", "-qm", "shorten")
        self.assertTrue(self.engine.touches_trust(git, base, "HEAD"))  # REVIEW.md is a trust name too
        self.assertEqual(self.engine.review_paths("## 민감한 파일\n\n없음\n"), [])

    def test_no_product_review_leaves_only_the_trust_names(self):
        repo, run, base, git = self._review_repo(None)
        (repo / "b.txt").write_text("b\n")
        run("add", "-A"), run("commit", "-qm", "b")
        self.assertFalse(self.engine.touches_trust(git, base, "HEAD"))

    def test_a_folder_pattern_covers_nested_copies_and_a_glob_crosses_folders(self):
        self.assertTrue(self.engine.sensitive("x/.claude/s.json", ".claude/"))
        self.assertTrue(self.engine.sensitive(".github/workflows/a/b.yml", ".github/workflows/**"))
        self.assertFalse(self.engine.sensitive("scripts/godot.envx", "scripts/godot.env"))


class LayoutTests(unittest.TestCase):
    def test_rules_live_in_agents_files_with_do_and_do_not(self):
        self.assertEqual(list(OFFICE.rglob("CLAUDE.md")), [])
        leader = (OFFICE / "leader/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## 할 일", leader)
        self.assertIn("## 하지 않을 일", leader)


if __name__ == "__main__":
    unittest.main()
