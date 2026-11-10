"""The office pipeline: stage order, sessions, permissions, approvals and stops, with a fake worker."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


OFFICE = Path(__file__).resolve().parent
ROOT = OFFICE.parent


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
            {"name": "search", "repo": str(repo), "test": self.test_cmd, "allow": ["Bash(npm test)"], "base": "main"}))
        (self.job / "INTENT.md").write_text("# search\n", encoding="utf-8")
        (repo / ".git/info/exclude").write_text("pass\n", encoding="utf-8")
        (repo / "pass").write_text("", encoding="utf-8")

    def state(self):
        return json.loads((self.job / "state.json").read_text())

    def test_full_flow_waits_for_people_and_keeps_contexts_apart(self):
        worker = FakeWorker(self.job)
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertEqual(self.state()["stage"], "approve")
        self.assertEqual(self.engine.approve("search"), 0)
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertEqual(self.state()["stage"], "pr")
        self.assertIn("exit 0", (self.job / "check.md").read_text())
        # An office checkout whose origin differs from the PR's repository, whatever this checkout's origin is.
        office = Path(self.tmp.name) / "office"
        subprocess.run(["git", "init", "-q", str(office)], check=True)
        subprocess.run(["git", "-C", str(office), "remote", "add", "origin", "https://github.com/me/office.git"], check=True)
        self.engine.BASELINE = office
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/1\n")
        self.assertEqual(self.engine.run("search", worker), self.engine.DONE)

        (design, d_argv, message), (implement, i_argv, _), (review, r_argv, _) = worker.calls
        self.assertEqual((design, implement, review), ("design", "implement", "review"))
        work = self.engine.session_id("search", "work")
        self.assertEqual(d_argv[d_argv.index("--session-id") + 1], work)
        self.assertEqual(i_argv[i_argv.index("--resume") + 1], work)
        # The reviewer starts a session of its own: an inherited conversation is not another context.
        self.assertNotIn(work, r_argv)
        self.assertIn("--session-id", r_argv)
        self.assertIn("design.prompt.md", message)
        for argv in (d_argv, i_argv, r_argv):
            self.assertEqual(argv[:5], ["claude", "--setting-sources", "project", "--permission-mode", "dontAsk"])
            self.assertIn(f"Edit(/{self.job}/task.json)", argv)
            self.assertIn("Read(**/.env*)", argv)
            # Reads stay in the task folder and this checkout: a bare Read or cat reaches credentials in the home folder.
            self.assertIn("--add-dir", argv)
            self.assertTrue(json.loads(argv[argv.index("--settings") + 1])["permissions"]["blockReadsOutsideWorkingDirectories"])
            self.assertNotIn("Read", argv)
            self.assertIn(f"Read(/{self.job}/**)", argv)
            # A wildcard fetch takes --upload-pa=<command> (git accepts short forms of long options) and refspecs.
            self.assertFalse([r for r in argv if " fetch" in r and "*" in r])
        self.assertNotIn("Bash(npm test)", d_argv)
        self.assertNotIn(f"Write(/{self.job}/repo/**)", d_argv)
        self.assertIn(f"Write(/{self.job}/repo/**)", i_argv)
        self.assertIn("Bash(npm test)", i_argv)
        self.assertFalse([r for r in d_argv if "commit" in r])
        self.assertTrue(any("review_preflight.py" in rule for rule in r_argv))
        for argv in (d_argv, i_argv, r_argv):
            # Write verbs name the product worktree: the worker's folder sits inside the office checkout.
            self.assertFalse([r for r in argv if r.startswith(("Bash(git add", "Bash(git commit", "Bash(git push", "Bash(gh pr checkout"))])
            self.assertIn(f"Write(/{self.job}/pr.txt)", argv)
            self.assertIn("Bash(gh api *-X*)", argv)
            # Bare Write/Edit would let a worker plant a git hook that the allowed commit then runs.
            self.assertFalse({"Write", "Edit"} & set(argv))
            self.assertIn("Write(**/.git/**)", argv)
            self.assertIn("Bash(git *--output*)", argv)
            self.assertIn("Bash(git *--upload-pack*)", argv)
            self.assertIn("Bash(git *--pathspec-f*)", argv)  # prints the lines of any file as pathspec errors
            self.assertIn("Bash(git -C repo difftool*)", argv)  # the diff allow rule also matches difftool -x <command>
            # git diff reads a path outside the repository without --no-index: absolute, ~, .., quoted or $VAR.
            for mark in (" /", " ~", "../", " ..", "/..", '"', "'", "$"):
                self.assertIn(f"Bash(git diff*{mark}*)", argv)
                self.assertIn(f"Bash(git -C repo diff*{mark}*)", argv)
        self.assertEqual(r_argv[r_argv.index("--add-dir") + 1], str(office))
        self.assertIn(f"Bash(git -C {self.job}/repo push origin HEAD)", r_argv)
        # commit takes -m first: git refuses -F with -m, and -F reads a file outside the task folder into the message.
        self.assertIn(f"Bash(git -C {self.job}/repo commit -m *)", i_argv)
        self.assertFalse([r for r in i_argv + r_argv if r.endswith("commit*)")])
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
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        task = json.loads((self.job / "task.json").read_text())
        task["test"] = "touch cache.pyc"
        (self.job / "task.json").write_text(json.dumps(task))
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertIn("커밋하지 않은 변경: 없음", (self.job / "check.md").read_text())

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
        self.engine.BASELINE = repo  # the office checkout and the product are the same repository
        (self.job / "design.md").write_text("d\n", encoding="utf-8")

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
        engine.BASELINE = ROOT
        own = engine.slug(subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"], capture_output=True, text=True).stdout)
        if own:  # a checkout with a GitHub origin
            self.assertIsNone(engine.unpinned(self.job, task))
        self.assertEqual(engine.slug("git@github.com:O/R.git\n"), "o/r")
        self.assertEqual(engine.slug("https://github.com/o/r/pull/1\n"), "o/r")
        self.assertIsNone(engine.slug("/tmp/r"))

    def test_task_names_are_safe_folder_and_terminal_names(self):
        for bad in ("../x", "검색", "A", "a" * 25):
            self.assertEqual(self.engine.new(bad, ".", "true", [], "main"), 2)

    def test_workers_reads_files_only(self):
        self.engine.run("search", FakeWorker(self.job))
        self.assertEqual(self.engine.workers(), 0)


class LayoutTests(unittest.TestCase):
    def test_office_is_not_copied_to_products(self):
        for manifest in (ROOT / "profiles/hash-copy").glob("repo-baseline*.json"):
            self.assertNotIn("claude-office", manifest.read_text(encoding="utf-8"))

    def test_rules_live_in_agents_files_with_do_and_do_not(self):
        self.assertEqual(list(OFFICE.rglob("CLAUDE.md")), [])
        leader = (OFFICE / "leader/AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## 할 일", leader)
        self.assertIn("## 하지 않을 일", leader)

    def test_ci_runs_the_office_tests(self):
        command = "python3 -m unittest discover -s claude-office -p 'test_*.py'"
        self.assertIn(command, (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8"))
        self.assertIn(command, (ROOT / "AGENTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
