"""The office pipeline: stage order, sessions, permissions, approvals and stops, with a fake worker."""

import contextlib
import importlib.util
import io
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
        self.baseline = Path(self.tmp.name) / "baseline"  # a repo-baseline clone outside the office checkout
        (self.baseline / "tools").mkdir(parents=True)
        self.engine.BASELINE = self.baseline
        if not shutil.which("sandbox-exec"):
            # Off macOS (CI), a stand-in drops `-p <profile>` and runs the command, so the flow tests still reach the
            # check stage. The real sandbox is tested only where sandbox-exec exists.
            bin_dir = Path(self.tmp.name) / "bin"
            bin_dir.mkdir()
            (bin_dir / "sandbox-exec").write_text('#!/bin/sh\nshift 2\nexec "$@"\n', encoding="utf-8")
            (bin_dir / "sandbox-exec").chmod(0o755)
            patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
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
        bin_dir.mkdir()
        log, draft = Path(self.tmp.name) / "gh.log", Path(self.tmp.name) / "draft"
        draft.write_text("true\n", encoding="utf-8")
        (bin_dir / "gh").write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:3] == ['pr', 'create']: print('https://github.com/O/R/pull/7')\n"
            f"if sys.argv[1:3] == ['pr', 'view']: print(open({str(draft)!r}).read().strip())\n", encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        patcher = unittest.mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        calls = lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []  # noqa: E731
        return bare, draft, calls

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
            # Bash and the tests it runs write the task folder and commits, not the engine's files or git settings.
            box = json.loads(argv[argv.index("--settings") + 1])["sandbox"]
            self.assertEqual([box[k] for k in ("enabled", "autoAllowBashIfSandboxed", "allowUnsandboxedCommands", "failIfUnavailable")],
                             [True, False, False, True])
            self.assertEqual(box["filesystem"]["allowWrite"][0], str(self.job))
            self.assertIn(str(self.engine.PIPELINE), box["filesystem"]["denyWrite"])
            deny = box["filesystem"]["denyWrite"]
            self.assertFalse([d for d in deny if str(self.job).startswith(d)])
            for name in ("task.json", "state.json", "pr.txt", "repo/.git", "CLAUDE.md"):
                self.assertIn(str(self.job / name), deny)
            self.assertIn(str(self.engine.OFFICE / "baseline.path"), deny)  # even before a person writes it
            if argv is d_argv:  # design ran with setUp's clone, outside the office checkout: deny all of it
                self.assertIn(str(self.baseline / "tools"), deny)
            self.assertNotIn(str(self.engine.JOBS), deny)
            self.assertIn(str(self.engine.OFFICE / "pipeline"), deny)
            self.assertEqual(argv[argv.index("--add-dir", argv.index("--add-dir") + 1) + 1], str(self.engine.OFFICE))
            self.assertIn(f"python3 {self.engine.OFFICE}/pipeline/engine.py preflight search", box["excludedCommands"])
            self.assertNotIn("Read", argv)
            self.assertIn(f"Read(/{self.job}/**)", argv)
            # A wildcard fetch takes --upload-pa=<command> (git accepts short forms of long options) and refspecs.
            self.assertFalse([r for r in argv if " fetch" in r and "*" in r])
        self.assertNotIn("Bash(npm test)", d_argv)
        self.assertNotIn(f"Write(/{self.job}/repo/**)", d_argv)
        self.assertIn(f"Write(/{self.job}/repo/**)", i_argv)
        self.assertIn("Bash(npm test)", i_argv)
        # Bash cd is refused while reads are blocked outside the working directories, so the engine runs the test in repo/.
        engine_test = f"Bash(python3 {self.engine.OFFICE}/pipeline/engine.py test search)"
        self.assertIn(engine_test, i_argv)
        self.assertIn(engine_test, r_argv)
        self.assertFalse([r for r in d_argv + i_argv + r_argv if r.startswith("Bash(cd")])
        self.assertFalse([r for r in d_argv if "commit" in r])
        # review_preflight.py reads the repository of its folder, and the worker's folder is inside the office checkout.
        self.assertIn(f"Bash(python3 {self.engine.OFFICE}/pipeline/engine.py preflight search)", r_argv)
        self.assertFalse([r for r in r_argv if "review_preflight.py" in r])
        # Without cd, a git rule lacking -C repo reads the office checkout instead of the product.
        allowed = [r for a in (d_argv, i_argv, r_argv) for r in a[a.index("--allowedTools"):a.index("--disallowedTools")]]
        self.assertFalse([r for r in allowed if r.startswith("Bash(git ") and not r.startswith("Bash(git -C ")])
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
        self.fake_github()
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        task = json.loads((self.job / "task.json").read_text())
        task["test"] = "touch cache.pyc"
        (self.job / "task.json").write_text(json.dumps(task))
        self.assertEqual(self.engine.run("search", worker), self.engine.WAIT)
        self.assertIn("커밋하지 않은 변경: 없음", (self.job / "check.md").read_text())

    def test_draft_stage_opens_one_pr_and_blocks_instead_of_forcing(self):
        bare, _, gh = self.fake_github()
        stage = {"id": "draft", "body": "pr-body.md", "out": "pr.txt"}
        task = json.loads((self.job / "task.json").read_text())
        body = self.job / "pr-body.md"
        for text in (None, "no title line\n", "# \n"):
            if text:
                body.write_text(text, encoding="utf-8")
            self.assertIn("# <PR title>", self.engine.open_draft(self.job, task, stage))
        self.assertEqual(gh(), [])
        # A branch of that name with other history on the remote: no force push, no PR.
        other = Path(self.tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "commit", "-q", "--allow-empty", "-m", "theirs"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "HEAD:refs/heads/office/search"], check=True, capture_output=True)
        body.write_text("# fix: x\n", encoding="utf-8")
        self.assertIn("git push failed", self.engine.open_draft(self.job, task, stage))
        self.assertEqual(gh(), [])
        # The engine reads the body outside the sandbox and posts it: no link out, no second name, no FIFO.
        secret = Path(self.tmp.name) / "credentials"
        secret.write_text("# token\n", encoding="utf-8")
        for make in (lambda: body.symlink_to(secret), lambda: os.link(secret, body), lambda: os.mkfifo(body)):
            body.unlink()
            make()
            self.assertIn("regular file", self.engine.open_draft(self.job, task, stage))
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
        self.assertIn(".github", self.engine.open_draft(self.job, {**task, "base": "origin/main"}, stage))
        self.assertIn("no branch gone", self.engine.open_draft(self.job, {**task, "base": "origin/gone"}, stage))
        self.assertEqual(gh(), [])
        # A URL already in pr.txt (an earlier run, or a person) is kept: nothing is pushed or opened.
        (self.job / "pr.txt").write_text("https://github.com/o/r/pull/1\n", encoding="utf-8")
        self.assertIsNone(self.engine.open_draft(self.job, task, stage))
        self.assertEqual(gh(), [])

    def test_check_stage_reads_the_clone_and_the_office_checkout(self):
        profile = self.engine.check_profile(self.job)
        for path in (self.baseline, self.engine.OFFICE):
            self.assertIn(json.dumps(str(path.resolve())), profile)

    def test_check_stage_blocks_without_sandbox_exec(self):
        worker = FakeWorker(self.job)
        self.engine.run("search", worker)
        self.engine.approve("search")
        which = shutil.which
        hide = lambda name, *a, **k: None if name == "sandbox-exec" else which(name, *a, **k)  # noqa: E731
        with unittest.mock.patch.object(self.engine.shutil, "which", hide):
            self.assertEqual(self.engine.run("search", worker), self.engine.BLOCKED)
        self.assertIn("sandbox-exec is missing", (self.job / "BLOCKED.md").read_text())
        self.assertFalse((self.job / "check.md").exists())

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
            self.assertEqual(self.engine.new(bad, ".", "true", [], "main"), 2)

    def test_claude_older_than_the_read_block_is_refused(self):
        too_old = self.engine.claude_too_old
        self.assertIsNone(too_old("2.1.257 (Claude Code)\n"))
        self.assertIsNone(too_old("2.10.0 (Claude Code)"))
        for version in ("2.1.256 (Claude Code)", "9.9.9", "fake 9.9.9 (Claude Code)", "", "unknown"):
            self.assertIn("2.1.257", too_old(version))

    def test_worker_starts_only_the_binary_whose_version_passed(self):
        fake, calls = Path(self.tmp.name) / "claude", []
        self.engine.hc = lambda *args, check=True: calls.append(args) or ""
        for script, started in (("#!/bin/sh\necho '2.1.256 (Claude Code)'\n", False), ("\0not a program", False),
                                ("#!/bin/sh\nprintf '\\377'\n", False),
                                ("#!/bin/sh\necho '2.1.257 (Claude Code)'\n", True)):
            fake.write_text(script, encoding="utf-8")
            fake.chmod(0o755)
            with unittest.mock.patch.dict(os.environ, {"PATH": f"{self.tmp.name}{os.pathsep}{os.environ['PATH']}"}):
                error = self.engine.run_worker("w", self.job, ["claude", "--flag"], "hi")
            self.assertEqual(error is None, started)
            self.assertEqual([c[-2:] for c in calls if c[0] == "run"], [(str(fake), "--flag")] if started else [])
        # A terminal left by an engine that died mid-stage is closed before the new one starts under the same name.
        start = [c[0] for c in calls].index("run")
        self.assertEqual([c[:2] for c in calls[start - 2:start]], [("kill", "w"), ("rm", "w")])

    def test_workers_do_not_inherit_the_leader_session_markers(self):
        seen = Path(self.tmp.name) / "env"
        fake = Path(self.tmp.name) / "hc"
        fake.write_text(f"#!/bin/sh\nenv > {seen}\n", encoding="utf-8")
        fake.chmod(0o755)
        markers = {m: "1" for m in self.engine.PARENT_MARKERS}
        with unittest.mock.patch.dict(os.environ, {"PATH": f"{self.tmp.name}{os.pathsep}{os.environ['PATH']}", **markers}):
            self.engine.hc("ls")
        names = {line.split("=", 1)[0] for line in seen.read_text().splitlines()}
        self.assertFalse(names & set(markers))
        self.assertIn("PATH", names)

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

    def test_a_worker_test_run_leaves_no_bytecode_for_the_check_to_count(self):
        (self.job / "repo/mod.py").write_text("X = 1\n")
        task = json.loads((self.job / "task.json").read_text())
        task["test"] = f"{shutil.which('python3')} -c 'import mod'"
        (self.job / "task.json").write_text(json.dumps(task))
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)  # an inherited value would pass this test without the engine
            self.assertEqual(self.engine.test("search"), 0)
            self.assertFalse((self.job / "repo/__pycache__").exists())

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
        self.assertEqual(kwargs["env"], self.engine.SAFE_GIT)  # its git reads the task's repository
        review = next(s for s in json.loads((OFFICE / "pipeline/stages.json").read_text())["flow"] if s["id"] == "review")
        self.assertIn("Bash(python3 {office}/pipeline/engine.py skills {name} *)", review["tools"])

    def test_engine_git_ignores_settings_planted_in_a_nested_repository(self):
        # Task code can commit a gitlink whose gitdir config sets core.fsmonitor; `git status` would run it.
        repo, evil = self.job / "repo", Path(self.tmp.name) / "evil"
        git = lambda *a, cwd=repo: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a],  # noqa: E731
                                                  cwd=cwd, check=True, capture_output=True)
        (repo / "sub").mkdir()
        git("init", "-q", "--separate-git-dir", str(evil), cwd=repo / "sub")
        (repo / "sub/f").write_text("x\n")
        git("add", "f", cwd=repo / "sub")
        git("commit", "-qm", "s", cwd=repo / "sub")
        git("add", "sub")
        git("commit", "-qm", "gitlink")
        marker = Path(self.tmp.name) / "PWNED"
        git("config", "core.fsmonitor", f"touch {marker}; false", cwd=repo / "sub")
        subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, env=self.engine.SAFE_GIT)
        self.assertFalse(marker.exists())
        subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True)
        self.assertTrue(marker.exists())  # the attack is real without the forced settings

    @unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox-exec")
    def test_check_stage_runs_the_test_where_it_writes_only_the_task_folder(self):
        outside = Path(self.tmp.name) / "outside.txt"  # a sibling of the task folder, still in the temp folder
        script = "; ".join(f"(echo x >> {p} && echo {name} ok || echo {name} denied)" for name, p in (
            ("repo", "kept.txt"), ("outside", outside), ("task", self.job / "task.json"), ("pr", self.job / "pr.txt"),
            ("gitfile", self.job / "repo/.git"), ("claude", self.job / "CLAUDE.md"))) + f'; echo x > "$TMPDIR/t" && echo tmp ok; (ls {Path.home()} >/dev/null 2>&1 && echo home ok || echo home denied)'
        script += f"; (mv {self.job / 'repo'} {self.job / 'moved'} 2>/dev/null && echo rename ok || echo rename denied)"
        result = self.engine.run_test(self.job, {"test": script}, sandboxed=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.split("\n")[:-1], ["repo ok", "outside denied", "task denied", "pr denied",
                                                         "gitfile denied", "claude denied", "tmp ok", "home denied",
                                                         "rename denied"])
        self.assertFalse(outside.exists() or (self.job / "pr.txt").exists() or (self.job / "CLAUDE.md").exists())

    @unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox-exec")
    def test_check_stage_gives_a_home_inside_the_task_folder(self):
        # The real home is unreadable there; without this HOME, git fails on ~/.gitconfig and tools fail writing their caches.
        script = 'git rev-parse HEAD >/dev/null && echo git ok; echo "$HOME"; echo x > "$HOME/c" && echo cache ok'
        result = self.engine.run_test(self.job, {"test": script}, sandboxed=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.split("\n")[:-1], ["git ok", str(self.job / "tmp/home"), "cache ok"], result.stderr)

    @unittest.skipUnless(shutil.which("sandbox-exec") and shutil.which("nc"), "macOS sandbox-exec")
    def test_check_stage_reaches_only_unix_sockets_in_the_task_folder(self):
        # A socket outside, such as /var/run/docker.sock, would let the test start a process outside the sandbox.
        (self.job / "tmp").mkdir()
        paths = {"inside": self.job.resolve() / "tmp/in.sock", "outside": Path(self.tmp.name).resolve() / "out.sock"}
        for path in paths.values():
            server = socket.socket(socket.AF_UNIX)
            self.addCleanup(server.close)
            server.bind(str(path))
            server.listen()
        script = "; ".join(f"(nc -U -w 1 {p} </dev/null 2>/dev/null && echo {n} ok || echo {n} denied)" for n, p in paths.items())
        result = self.engine.run_test(self.job, {"test": script}, sandboxed=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.split("\n")[:-1], ["inside ok", "outside denied"])

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
