"""The office pipeline: stage order, sessions, permissions, approvals and stops, with a fake worker."""

import contextlib
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
import unittest.mock


OFFICE = Path(__file__).resolve().parent


def load_engine(jobs):
    os.environ["OFFICE_JOBS"] = str(jobs)
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
        (bin_dir / "gh").write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            "stdin = [sys.stdin.read()] if sys.argv[1:3] == ['pr', 'edit'] else []\n"
            f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:] + stdin) + '\\n')\n"
            "if sys.argv[1:3] == ['pr', 'create']: print('https://github.com/O/R/pull/7')\n"
            f"if sys.argv[1:3] == ['pr', 'view']: print(open({str(draft)!r}).read().strip())\n"
            f"if sys.argv[1:3] == ['pr', 'ready']: open({str(draft)!r}, 'w').write('false')\n", encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        calls = lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []  # noqa: E731
        return draft, calls

    def test_full_flow_waits_for_people_and_keeps_contexts_apart(self):
        bare, draft, gh = self.fake_github()
        worker = FakeWorker(self.job)
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertEqual(self.state()["stage"], "approve")
        self.assertEqual(self.engine.approve("search"), 0)
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertEqual(self.state()["stage"], "pr")
        self.assertIn("exit 0", (self.job / "check.md").read_text())
        # The engine pushed the task branch and opened a draft from the worker's body; the review waits for a person.
        head = subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        self.assertEqual(subprocess.run(["git", "-C", str(bare), "rev-parse", "office/search"], capture_output=True, text=True).stdout, head)
        create = gh()[0]
        self.assertEqual(create[:3], ["pr", "create", "--repo"])
        self.assertEqual([create[create.index(f) + 1] for f in ("--repo", "--head", "--base", "--title", "--body")],
                         ["o/r", "office/search", "main", "fix: x", "## Summary\n"])
        self.assertIn("--draft", create)
        self.assertEqual((self.job / "pr.txt").read_text(), "https://github.com/O/R/pull/7\n")
        self.assertNotIn("branch.office/search.remote", subprocess.run(
            ["git", "-C", str(self.job / "repo"), "config", "--list"], capture_output=True, text=True).stdout)  # no -u
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)  # still a draft
        self.assertEqual([c[:2] for c in gh()], [["pr", "create"], ["pr", "view"], ["pr", "view"]])
        draft.write_text("false\n", encoding="utf-8")
        # An office checkout whose origin differs from the PR's repository, whatever this checkout's origin is.
        office = Path(self.tmp.name) / "office"
        subprocess.run(["git", "init", "-q", str(office)], check=True)
        subprocess.run(["git", "-C", str(office), "remote", "add", "origin", "https://github.com/me/office.git"], check=True)
        self.engine.BASELINE = office
        self.assertEqual(self.engine.run("search", worker), self.engine.DONE)
        self.assertEqual([c[:2] for c in gh()].count(["pr", "create"]), 1)
        # The engine, not the reviewer, posts the review's body: as bytes on stdin to the pr.txt URL.
        self.assertEqual(gh()[-1], ["pr", "edit", "https://github.com/O/R/pull/7", "--body-file", "-",
                                    "## Summary\n\n## Review\nok\n"])

        (design, d_argv, message), (implement, i_argv, _), (review, r_argv, _) = worker.calls
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
            for rule in ("Bash(git push*)", "Bash(git * push*)", "Bash(gh pr merge*)", "Bash(gh pr edit*)", "Bash(gh api *-X*)"):
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
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertIn("커밋하지 않은 변경: 없음", (self.job / "check.md").read_text())

    def test_draft_stage_marks_only_an_easy_pr_ready(self):
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
                self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
                self.assertEqual(self.engine.approve("search"), 0)
                self.engine.run("search", worker)
                calls = [c[:2] for c in gh()]
                self.assertEqual(calls.count(["pr", "ready"]), int(ready))
                # The review's model follows the same proof: sonnet for this checked commit only when it was easy.
                self.assertEqual(self.state().get("easy_pr") == self.state()["checked"], bool(ready))
                if ready:
                    self.assertIn(["pr", "ready", "https://github.com/O/R/pull/7"], gh())
                    self.assertEqual(self.state()["stage"], "review")  # past the person's Ready
                else:
                    self.assertEqual(self.state()["stage"], "pr")

    def test_draft_stage_marks_ready_only_the_approved_design(self):
        # The same easy text, but not the bytes a person approved: the PR stays a draft.
        _, _, gh = self.fake_github()
        (self.job / "design.md").write_text("Door: 되돌리기 쉬움\n", encoding="utf-8")
        body = "# fix: x\n\nDoor: 되돌리기 쉬움\n신뢰 경계: 없음\n".encode()
        task = json.loads((self.job / "task.json").read_text())
        head = subprocess.run(["git", "-C", str(self.job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        stage = {"id": "draft", "body": "pr-body.md", "out": "pr.txt", "easy": "design.md"}
        self.assertIsNone(self.engine.open_draft(self.job, task, stage, body, head, "0" * 64))
        self.assertEqual([c[:2] for c in gh()], [["pr", "create"]])
        (self.job / "pr.txt").unlink()
        approved = hashlib.sha256((self.job / "design.md").read_bytes()).hexdigest()
        self.assertIsNone(self.engine.open_draft(self.job, task, stage, body, head, approved))
        self.assertEqual([c[:2] for c in gh()], [["pr", "create"], ["pr", "create"], ["pr", "ready"]])

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
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        # A rerun after a blocked draft: a commit made since the check goes through the check again before the push.
        (self.job / "pr.txt").unlink()
        self.engine.save(self.job / "state.json", {**self.state(), "stage": "draft"})
        repo = self.job / "repo"
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", "after the check"], check=True)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
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
            self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
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
        self.assertEqual(names, [f"{self.engine.TAG}-search-design"])

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
        self.assertEqual(diff[0], ["git", "-C", f"{self.job}/repo", "diff", f"refs/remotes/origin/main...{head}", "--", ".", ":!.env*", ":!**/.env*"])
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
        self.assertEqual(argv[0][1:], ["-I", script, "refs/remotes/origin/main", "--harness", "claude"])
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

    def test_doctor_says_what_the_product_lacks(self):
        _, calls = self.fake_gh()  # gh answers 0: logged in, main protected
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

    def routed(self, design_text, fail_haiku=None):
        """Run to the draft wait with a design that says design_text; fail_haiku: "silent" or "blocked" for a haiku implement."""
        job = self.job

        class Worker(FakeWorker):
            def __call__(self, name, cwd, argv, message):
                stage = name.rsplit("-", 1)[1]
                model = argv[argv.index("--model") + 1]
                if stage == "implement" and model == "haiku" and fail_haiku:
                    self.calls.append((stage, argv, message))
                    if fail_haiku == "blocked":
                        (job / "BLOCKED.md").write_text("gave up\n", encoding="utf-8")
                    return None
                result = super().__call__(name, cwd, argv, message)
                if stage == "design":
                    (job / "design.md").write_text(design_text, encoding="utf-8")
                return result

        worker = Worker(job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.run("search", worker)
        return [(c[0], c[1][c[1].index("--model") + 1], c[1][c[1].index("--resume" if "--resume" in c[1] else "--session-id") + 1])
                for c in worker.calls]

    def test_each_stage_gets_its_model_from_the_approved_difficulty(self):
        work = self.engine.session_id("search", "work")
        self.assertEqual(self.routed("# d\n\n난이도: 쉬움\n"), [("design", "sonnet", work), ("implement", "haiku", work)])
        self.assertEqual(self.state()["models"], [{"stage": "design", "model": "sonnet", "rerun": False},
                                                  {"stage": "implement", "model": "haiku", "rerun": False}])
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
                self.assertEqual([m for _, m, _ in self.routed(text)], ["sonnet", "sonnet"])

    def test_the_review_runs_on_opus_unless_the_pr_proved_easy(self):
        stage = next(s for s in json.loads(self.engine.STAGES.read_text())["flow"] if s["id"] == "review")
        flow = json.loads(self.engine.STAGES.read_text())["flow"]
        self.assertEqual(self.engine.stage_model(stage, self.job, {}, flow), "opus")
        self.assertEqual(self.engine.stage_model(stage, self.job, {"easy_pr": "abc", "checked": "abc"}, flow), "sonnet")
        self.assertEqual(self.engine.stage_model(stage, self.job, {"easy_pr": "abc", "checked": "def"}, flow), "opus")  # a later commit
        self.assertNotIn("haiku", [self.engine.stage_model(stage, self.job, st, flow) for st in ({}, {"easy_pr": "a", "checked": "a"})])

    def test_a_failed_haiku_implement_reruns_once_on_sonnet_in_a_new_session(self):
        work, fresh = self.engine.session_id("search", "work"), self.engine.session_id("search", "work-sonnet")
        for mode in ("silent", "blocked"):
            with self.subTest(mode=mode):
                if mode == "blocked":
                    self.setUp()
                calls = self.routed("난이도: 쉬움\n", fail_haiku=mode)
                self.assertEqual(calls, [("design", "sonnet", work), ("implement", "haiku", work), ("implement", "sonnet", fresh)])
                self.assertEqual(self.state()["models"][-1], {"stage": "implement", "model": "sonnet", "rerun": True})
                self.assertIn("exit 0", (self.job / "check.md").read_text())  # the sonnet run passed the check
                self.assertNotIn("gave up", (self.job / "BLOCKED.md").read_text() if (self.job / "BLOCKED.md").exists() else "")
        self.assertTrue((self.job / "BLOCKED.haiku.md").exists())

    def test_workers_reads_files_only(self):
        self.engine.run("search", FakeWorker(self.job))
        self.assertEqual(self.engine.workers(), 0)


class LayoutTests(unittest.TestCase):
    def test_rules_live_in_agents_files_with_do_and_do_not(self):
        self.assertEqual(list(OFFICE.rglob("CLAUDE.md")), [])
        leader = (OFFICE / "leader/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## 할 일", leader)
        self.assertIn("## 하지 않을 일", leader)


if __name__ == "__main__":
    unittest.main()
