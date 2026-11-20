#!/usr/bin/env python3
"""Move office tasks through stages.json. Workers run in hc terminals; this script only reads and writes files.

  engine.py doctor <repo>     check that a product repository can connect (AGENTS.md cells pass on main, protection, gh,
                              repo-baseline's GitHub contract by its apply_github_settings.py plan)
  engine.py new <task> --repo PATH [--base origin/main]  test = the product AGENTS.md check && test cells; runs setup once
  engine.py run <task>        advance until a person is needed, the task is blocked, or it is done
  engine.py approve <task>    a person approves the current design file (bound to its sha256); run approves an easy one itself
  engine.py workers           show every task's stage from files only
  engine.py board             write jobs/board.html: an office of this pipeline's tasks beside the stages and cards (run also rewrites it)
  engine.py test <task>       run the task's test command in its repo/, full output in test.log
  engine.py preflight <task>  run repo-baseline's review preflight for pr.txt in its repo/, diff into pr.diff
  engine.py skills <task> <base>  run matt-skills check_skills.py in its repo/ (exit 0 delegate, 1 missing, 2 do not)

After the check passes, run pushes the task branch, opens a PR from pr-body.md, marks it ready and, unless it proved easy, asks the review bot once.
After the review, run replaces the PR body with review-body.md, writes REPORT.md, waits for CI and queues a PR that needs no person.
People act only to approve a hard design and to merge a hard PR.
Once a person has put a finished task's PR in the merge queue, workers and the board write BLOCKED.md if it leaves unmerged,
and move that note aside once the PR merges or a person puts it back in the queue.

Exit codes for run: 0 done, 2 no such task or another run of it is going, 3 waiting for a person, 4 blocked.
"""

import argparse
import base64
import errno
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
OFFICE = PIPELINE.parent
# The repo-baseline clone whose prompts/ and tools/ the stages use. A person writes its path in baseline.path
# (git ignores it). Without the file, main() stops before any command but workers.
_PATH = OFFICE / "baseline.path"
BASELINE = Path(_PATH.read_text(encoding="utf-8").strip()).expanduser() if _PATH.exists() else None
# A relative or empty path would follow the working folder; a worker runs preflight from its task folder.
if BASELINE is not None and not BASELINE.is_absolute():
    sys.exit(f"{_PATH} must hold the absolute path of the repo-baseline clone")
BASELINE = BASELINE and BASELINE.resolve()
JOBS = Path(os.environ.get("OFFICE_JOBS", OFFICE / "jobs"))
STAGES = Path(os.environ.get("OFFICE_STAGES", PIPELINE / "stages.json"))
DONE, WAIT, BLOCKED = 0, 3, 4
# Errors a second try may clear (network, GitHub, hc): the engine retries them once on its own, after this many seconds.
TRANSIENT = ("git push failed", "gh pr create failed", "gh pr edit failed", "gh pr ready failed", "gh pr comment failed",
             "gh pr view", "hc run failed")
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
RETRY_WAIT = float(os.environ.get("OFFICE_RETRY_WAIT", "10"))
# A worker turn longer than this is taken as hung: the engine closes it and blocks the task.
WORKER_LIMIT = float(os.environ.get("OFFICE_WORKER_TIMEOUT", str(4 * 3600)))
BOARD_EVERY = float(os.environ.get("OFFICE_BOARD_EVERY", "5"))  # seconds between board rewrites while a run goes
# The ci stage waits this long at most for the PR's checks, asking gh every CI_EVERY seconds; none after CI_NONE seconds is no CI.
CI_LIMIT = float(os.environ.get("OFFICE_CI_TIMEOUT", str(3600)))
CI_EVERY = float(os.environ.get("OFFICE_CI_EVERY", "30"))
CI_NONE = 300
# GitHub may still report the head before the engine's push for a moment; a different head after this is a real one.
CI_MOVED = 60
MERGE_EVERY = 60  # seconds between asking gh how an open PR stands (merged, behind, in conflict, its CI)
# hc terminal names are global to the hc core; this tag keeps another checkout's jobs folder from sharing them.
TAG = hashlib.sha256(str(JOBS.resolve()).encode()).hexdigest()[:4]


class NotStarted(str):
    """An error from before the worker started: its session was never created, so the next try must not --resume."""


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, data):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)  # the board reads state.json every few seconds: never let it see half a file


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def session_id(task, key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"claude-office:{task}:{key}"))


def fill(text, job, task):
    values = {"job": job, "office": OFFICE, "baseline": BASELINE, "name": task["name"], "branch": f"office/{task['name']}",
              "test": task["test"]}
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def session_key(stage, state):
    """A stage rerun with sonnet after haiku failed starts a session of its own: the failed one's context stays behind."""
    return stage["session"] + ("-sonnet" if stage["id"] in state.get("escalated", []) else "")


def difficulty(job, state, flow):
    """쉬움, 보통 or 어려움 from the approved design's one `난이도:` line, else None (missing, invalid, or not the approved bytes)."""
    stage = next((s for s in flow if s.get("approve")), None)
    if not stage or sha(job / stage["approve"]) != state.get("approved", {}).get(stage["id"]):
        return None
    lines = [l.strip() for l in unicodedata.normalize("NFKC", (job / stage["approve"]).read_text(encoding="utf-8", errors="replace")).splitlines()
             if "난이도" in l and not re.match(r"\s*#+\s*\S+\s*$", l)]
    found = {re.fullmatch(r"난이도: (쉬움|보통|어려움)", l) and l.split(": ")[1] for l in lines}
    return found.pop() if len(found) == 1 and None not in found else None


def proven_easy(state):
    """The draft stage proved the PR easy for the commit that was checked and pushed: a rerun of the check drops it.
    The review's commits come after and do not drop it: enqueue checks their paths again."""
    return bool(state.get("easy_pr")) and state.get("easy_pr") == state.get("checked")


def stage_model(stage, job, state, flow):
    """The model a worker stage runs on. Workers get --setting-sources project, so the user's model setting never reaches them.
    implement: haiku for 쉬움, else sonnet, and sonnet after a failed haiku run. review: opus unless the PR is provably easy."""
    route = stage.get("model", "sonnet")
    if route == "difficulty":
        return "sonnet" if stage["id"] in state.get("escalated", []) or difficulty(job, state, flow) != "쉬움" else "haiku"
    if route == "door":
        return "sonnet" if proven_easy(state) else "opus"
    return route


def stage_effort(stage, model, state):
    """Per stage and model: the same model may think harder in one stage than another.
    A stage that failed on a model other than haiku reruns one level higher: effort before a bigger model."""
    effort = stage.get("effort", {}).get(model)
    if effort and stage["id"] in state.get("raised", []):
        effort = EFFORTS[min(EFFORTS.index(effort) + 1, len(EFFORTS) - 1)]
    return effort


def claude_argv(config, stage, job, task, state):
    """The worker command: a new session for the first use of a session key, --resume after that."""
    key = session_key(stage, state)
    flag = "--resume" if key in state["sessions"] else "--session-id"
    model = stage_model(stage, job, state, config["flow"])
    argv = [*(fill(arg, job, task) for arg in config["runner"]), flag, session_id(task["name"], key), "--model", model]
    if effort := stage_effort(stage, model, state):
        argv += ["--effort", effort]
    # Normal development tools; deny rules hold in every permission mode, so only the engine pushes or edits the PR.
    return argv + ["--allowedTools", *config["allow"], "--disallowedTools", *config["deny"]]


def hc(*args, check=True):
    return subprocess.run(["hc", *args], capture_output=True, text=True, check=check).stdout


def run_worker(name, cwd, argv, message):
    """Start an interactive worker in hc, give it one short message, wait for the turn, then end it."""
    # A terminal of this name is left over from an engine that died mid-stage: run() holds the task lock and the name
    # carries this jobs folder's TAG, so no live engine owns it. Its worker may still be writing: closing it is what a second try of the stage needs anyway.
    hc("kill", name, check=False)
    hc("rm", name, check=False)
    try:
        # Without --no-parent, a leader in an hc terminal would own the worker: hc types notes into it, and the worker ends with it.
        hc("run", "--bg", "--no-parent", "--name", name, "--cwd", str(cwd), *argv)
    except subprocess.CalledProcessError as error:
        return NotStarted(f"hc run failed: {(error.stderr or error.stdout or '').strip()}")
    try:
        hc("wait", "--timeout", "90", name, check=False)
        if "trust this folder" in hc("read", "--lines", "0", name, check=False):
            return NotStarted("folder is not trusted: open Claude Code here once and trust it")
        # A task passed on the command line is dropped after a dialog or --resume, so send it after start.
        reply = json.loads(hc("send", "--wait", name, message, check=False) or "{}").get("wait") or {}
        deadline = time.monotonic() + WORKER_LIMIT
        while reply.get("reason") == "timeout":
            if time.monotonic() > deadline:
                return f"the worker was still working after {WORKER_LIMIT:g}s (OFFICE_WORKER_TIMEOUT): the engine closed it"
            reply = json.loads(hc("wait", name, check=False) or "{}")
        # A usage limit ends the turn with hc status error ("You've hit your 5-hour limit · resets 12:10am"):
        # a block, not a failed stage, so it neither spends the two tries nor moves haiku to sonnet.
        status = json.loads(hc("get", name, check=False) or "{}").get("status") or {}
        if isinstance(status, dict) and status.get("state") == "error":
            # The message first: workers and the board show the first 60 characters, and the reset time is what matters.
            return f"{status.get('msg') or 'no message'} (the worker ended in hc status error). Run the task again once that clears"
        return None
    finally:
        hc("kill", name, check=False)  # close the session so the next stage can resume it
        hc("rm", name, check=False)


def block(job, reason):
    (job / "BLOCKED.md").write_text(f"# 막힘\n\n{reason}\n", encoding="utf-8")
    print(f"blocked: {reason}")
    return BLOCKED


def slug(url):
    """owner/repo of a github.com URL (https, ssh, scp-style git@github.com:o/r or a PR link), else None.
    The host is parsed, not searched for: notgithub.com and github.com in another host's path are not GitHub."""
    url = (url.split() or [""])[0]
    scp = re.fullmatch(r"[\w.-]+@github\.com:([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url)
    if scp:
        parts = list(scp.groups())
    else:
        try:
            parsed = urllib.parse.urlsplit(url)
            host = parsed.hostname
        except ValueError:
            return None
        parts = parsed.path.strip("/").split("/")[:2]
        if parsed.scheme not in ("https", "http", "ssh", "git") or host != "github.com" or len(parts) < 2:
            return None
        parts[1] = re.sub(r"\.git$", "", parts[1])
    return "/".join(parts).lower() if all(re.fullmatch(r"[\w.-]+", part) for part in parts) else None


def unpinned(job, task):
    """Why these checkouts may not review the task's PR, else None.

    The review worker gets its prompt, permissions and preflight script from the office and repo-baseline checkouts,
    and REVIEW.md from repo-baseline. For a PR of the same repository as either one, that checkout must be
    origin/main's copy, or the PR could rewrite its own review. Only a PR that provably belongs to another
    repository skips the comparison.
    """
    def git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)

    pr_file = job / "pr.txt"  # the draft stage or a person wrote it; workers cannot edit it
    pr = slug(pr_file.read_text(encoding="utf-8")) if pr_file.exists() else None
    product = git(task["repo"], "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    for root in (BASELINE, OFFICE):  # the same repository twice gives the same answer twice
        own = slug(git(root, "remote", "get-url", "origin").stdout)
        common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        if pr and own and pr != own and common and product and common != product:
            continue
        git(root, "fetch", "-q", "origin", "main")  # offline: the comparison below uses the last fetched main
        # A missing origin/main makes diff fail, which also blocks. Ignored files (jobs/) are not compared.
        if git(root, "diff", "--quiet", "origin/main").returncode or git(
                root, "status", "--porcelain", "--untracked-files=all").stdout.strip():
            return (f"the PR under review is not known to belong to another repository, and {root} differs from "
                    "origin/main: run the task from a clean checkout at origin/main")
    return None


def read_body(body):
    """The file's bytes, or b"" when it is missing or over 60000 bytes (GitHub's PR body limit is 65536 characters)."""
    return body.read_bytes() if body.is_file() and body.stat().st_size <= 60000 else b""


def open_draft(job, task, stage, raw, head, approved=None, seen=None):
    """Push the task branch and open a ready PR with the body the implement worker wrote, else why not.
    Not a draft: opening a draft and marking it ready a second later ran each PR workflow twice and the cancelled run left a failed check.
    raw and head are what the caller already checked (the body's bytes, the tested commit): nothing here reads them again.
    approved is the sha256 a person approved for the stage's easy file. seen["easy"] says whether the PR proved easy."""
    pr_file = job / stage["out"]
    if pr_file.exists():  # an earlier run opened it, or a person did and wrote the URL
        return None
    body = job / stage["body"]
    title, _, text = raw.decode("utf-8", "replace").partition("\n")
    if not title.startswith("# ") or not title[2:].strip():
        return f"{body} must be a file of at most 60000 bytes that starts with a `# <PR title>` line"
    git = lambda *a: subprocess.run(["git", "-C", str(job / "repo"), *a], capture_output=True, text=True)  # noqa: E731
    repo = slug(git("config", "--get", "remote.origin.url").stdout)  # as written: url.*.insteadOf may point elsewhere
    if not repo:
        return "the product repository's origin is not on GitHub: open the PR and write its URL into pr.txt"
    # A pushed branch runs the repository's workflows, with their secrets: a person pushes changes to them.
    # Compare with the base commit the remote has now, not a local ref.
    base = task["base"].removeprefix("origin/")
    remote = git("ls-remote", "origin", f"refs/heads/{base}").stdout.split()[:1]
    if not remote:
        return f"origin has no branch {base} to compare the task branch with"
    git("fetch", "-q", "origin", f"refs/heads/{base}")  # others may have pushed to it since this checkout was made
    ci = git("diff", "--name-only", "--ignore-submodules=none", f"{remote[0]}...{head}", "--", ".github")  # the commit that is pushed below
    if ci.returncode or not head:
        return f"could not compare the task branch with origin's {base}: {ci.stderr.strip()}"
    if ci.stdout.strip():
        return (f"the branch changes .github ({ci.stdout.split()}): a person reads the diff, "
                "pushes the branch, opens the PR and writes its URL into pr.txt")
    branch = f"office/{task['name']}"
    # No -u: it writes the product repository's shared config. No force: a diverged branch of that name stops here.
    push = git("push", "origin", f"{head}:refs/heads/{branch}")
    if push.returncode:
        return f"git push failed: {push.stderr.strip()}"
    made = subprocess.run(["gh", "pr", "create", "--repo", repo, "--head", branch,
                           "--base", base, "--title", title[2:].strip(),
                           "--body", text.lstrip("\n")], cwd=job, capture_output=True, text=True)
    url = (made.stdout.strip().splitlines() or [""])[-1]
    if made.returncode or not re.fullmatch(rf"https://github\.com/{re.escape(repo)}/pull/\d+", url, re.I):
        return f"gh pr create failed: {(made.stderr or made.stdout).strip()}"
    pr_file.write_text(url + "\n", encoding="utf-8")
    # The PR opens ready; the ready stage marks a person's draft PR ready. Easy (proven here) only picks the review's model and the report's note:
    # a hard or trust-boundary PR needs a person to read its diff before the merge.
    if stage.get("easy"):
        design = read_body(job / stage["easy"])  # only the approved bytes (a person's, or the approve stage's for an easy design)
        guarded = touches_trust(git, remote[0], head)
        if approved and hashlib.sha256(design).hexdigest() == approved and easy(design, raw) and not guarded:
            if seen is not None:
                seen["easy"] = True
    return None


def touches_trust(git, base, head):
    """The branch base...head changes or moves a TRUST_NAMES path or a path the product REVIEW.md lists as sensitive
    (read at base, so the branch cannot shorten the list), or git could not tell (fails closed)."""
    # -z: raw names, not C-quoted ones; --no-renames: a rename out of a trust path shows its old name too
    names = git("diff", "--name-only", "-z", "--no-renames", "--ignore-submodules=none", f"{base}...{head}")
    if names.returncode:
        return True
    changed = [n for n in names.stdout.split("\0") if n]
    if any(part.casefold() in TRUST_NAMES for n in changed for part in n.split("/")):
        return True
    review = git("show", f"{base}:REVIEW.md")
    # No REVIEW.md at base: only TRUST_NAMES apply. Any other git error fails closed.
    if review.returncode:
        return not re.search(r"does not exist|exists on disk, but not in", review.stderr)
    return any(sensitive(n, pattern) for n in changed for pattern in review_paths(review.stdout))


def review_paths(text):
    """Backquoted paths in the 민감한 파일 section of a product REVIEW.md (repo-baseline REVIEW template)."""
    # Any heading level; every backquoted text counts (a bare `Makefile`, a path with spaces):
    # an extra pattern only sends more PRs to a person
    paths = []
    # Every such heading (a 민감한 파일 예시 before the real one must not hide it). A section ends at a heading
    # of its own level or higher: its subheadings stay in it
    for start in re.finditer(r"^(#+)[ \t]*민감한 파일[^\n]*\n", text, re.M):
        end = re.search(rf"^#{{1,{len(start.group(1))}}}[ \t]", text[start.end():], re.M)
        section = text[start.end():start.end() + end.start()] if end else text[start.end():]
        paths += [p.strip() for p in re.findall(r"`([^`\n]+)`", section) if p.strip()]
    return paths


def sensitive(name, pattern):
    """A folder pattern (ends with /) covers everything under it at any depth; otherwise a glob, where * and ** cross /,
    or a folder written without its / (`src/auth`). A leading ./ or / means the repository root."""
    rooted = pattern.startswith(("./", "/"))
    pattern = pattern.removeprefix("./").lstrip("/")
    if pattern.endswith("/"):  # the folder itself too: a submodule pointer has no / after it
        return f"{name}/".startswith(pattern) if rooted else f"/{pattern}" in f"/{name}/"
    # /name: **/x covers a root-level x too
    return any(fnmatch.fnmatchcase(n, pattern) for n in (name, f"/{name}")) or name.startswith(f"{pattern}/")


# Paths a worker could use to steer later sessions or tools, whatever the body claims: those PRs wait for a person.
TRUST_NAMES = {n.casefold() for n in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude", ".mcp.json", ".envrc",
                                      ".github", ".agents", ".codex", ".cursor", "REVIEW.md")}


def easy(design, body):
    """True only when every revert claim in the approved design and in the PR body says exactly 되돌리기: 쉬움
    (or the old Door: 되돌리기 쉬움), there is one, and the body has exactly one sensitive-files line: 민감한 파일: 없음
    (or the old 신뢰 경계: 없음). A claim is any line naming the old words, or a line with the new word and a colon
    after it (되돌리기 판정: 어려움 and 참고: 되돌리기: 어려움 count): the new words are ordinary Korean that prose uses too. Fails closed:
    list markers, emphasis or a second claim anywhere leave the PR not easy."""
    sensitive = claims(body, TRUST_CLAIM, {"민감한 파일: 없음", "신뢰 경계: 없음"})
    return bool(door_easy(design) and door_easy(body) and sensitive and len(sensitive) == 1)


DOOR_CLAIM = r"d\W*o\W*o\W*r|되\W*돌\W*리\W*기[^:]*:"
TRUST_CLAIM = r"신뢰\W*경계|민감한\W*파일[^:]*:"


def claims(raw, word, values):
    """Every line of raw that makes a claim with word (headings aside), if there is one and all are in values, else None."""
    found = [l.strip() for l in unicodedata.normalize("NFKC", raw.decode("utf-8", "replace")).splitlines()
             if re.search(word, l, re.I) and not re.match(r"\s*#+\s*\S+\s*$", l)]
    return found if found and set(found) <= values else None


def door_easy(raw):
    """Every revert claim in raw says exactly 되돌리기: 쉬움 (or the old Door: 되돌리기 쉬움), and there is one."""
    return bool(claims(raw, DOOR_CLAIM, {"되돌리기: 쉬움", "Door: 되돌리기 쉬움"}))


def push_review(job, task):
    """Push the review worker's commits to the PR branch, else why not. Only the engine pushes (workers' push is denied)."""
    git = lambda *a: subprocess.run(["git", "-C", str(job / "repo"), *a], capture_output=True, text=True)  # noqa: E731
    # The engine names the branch: the worktree's HEAD or local refs may have moved.
    branch = f"refs/heads/office/{task['name']}"
    remote = git("ls-remote", "origin", branch).stdout.split()[:1]
    if not remote:
        return f"origin has no {branch}: a person pushes the review's commits to the PR branch"
    head = git("rev-parse", "HEAD").stdout.strip()
    if head == remote[0]:
        return None
    git("fetch", "-q", "origin", branch)
    # Only what the review added: the draft stage or a person already let the rest of the branch onto GitHub.
    ci = git("diff", "--name-only", "--ignore-submodules=none", remote[0], head, "--", ".github")
    if ci.returncode or not head:
        return f"could not compare the review's commits with origin's {branch}: {ci.stderr.strip()}"
    if ci.stdout.strip():
        return (f"the review's commits change .github ({ci.stdout.split()}): a person reads the diff and pushes them")
    push = git("push", "origin", f"{head}:{branch}")  # no force: a diverged branch stops here
    if push.returncode:
        return f"git push failed: {push.stderr.strip()}"
    return None


def post_body(job, task, stage):
    """Push the review's commits, then replace the PR body with the file the review worker wrote, else why not."""
    body = job / stage["edit"]
    raw = read_body(body)
    pr_file = job / "pr.txt"  # the draft stage or a person wrote it
    url = pr_file.read_text(encoding="utf-8").split()[:1] if pr_file.exists() else []
    if not raw.strip():
        return f"{body} must be a file of 1 to 60000 bytes with the whole PR body: the review worker writes it"
    if not url or not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", url[0]):
        return f"{pr_file} must hold the PR's GitHub URL"
    # The body names the reviewed head, and the owner merges that SHA: it goes up only once the commits are there.
    if error := push_review(job, task):
        return error
    edit = subprocess.run(["gh", "pr", "edit", url[0], "--body-file", "-"], input=raw, cwd=job, capture_output=True)
    if edit.returncode:
        return f"gh pr edit failed: {(edit.stderr or edit.stdout).decode(errors='replace').strip()}"
    return None


def make_ready(job, stage, state):
    """Mark the PR in pr.txt ready and, unless it proved easy, ask the review bot once (POLICY §3), else why not.
    POLICY asks only when the bot skipped a hard PR; the bot may not have run yet here, so every hard PR asks once."""
    pr_file = job / stage["ready"]
    if not pr_file.exists():
        return f"{pr_file} is missing: the draft stage did not open a PR"
    url = pr_file.read_text(encoding="utf-8").split()[:1]
    view = subprocess.run(["gh", "pr", "view", *url, "--json", "isDraft", "--jq", ".isDraft"], cwd=job, capture_output=True, text=True)
    if view.returncode:
        return f"gh pr view {' '.join(url)} failed: {view.stderr.strip()}"
    if view.stdout.strip() != "false":
        ready = subprocess.run(["gh", "pr", "ready", *url], cwd=job, capture_output=True, text=True)
        if ready.returncode:
            return f"gh pr ready failed: {(ready.stderr or ready.stdout).strip()}"
    if not proven_easy(state):
        return ask_bot(job, url[0], stage.get("request"), state)
    return None


def ask_bot(job, url, request, state):
    """Ask the review bot once per PR, else why not."""
    if not request or state.get("requested") == url:
        return None
    asked = subprocess.run(["gh", "pr", "comment", url, "--body", request], cwd=job, capture_output=True, text=True)
    if asked.returncode:
        return f"gh pr comment failed: {(asked.stderr or asked.stdout).strip()}"
    state["requested"] = url
    return None


def review_says_easy(job):
    """The review's 「리뷰한 head와 되돌리기」 line (old: 「리뷰한 head와 Door」) says 쉬움; a missing or other line is hard (POLICY §3)."""
    review = (job / "review.md").read_text(encoding="utf-8", errors="replace") if (job / "review.md").exists() else ""
    line = re.search(r"리뷰한 head와 (?:되돌리기|Door):([^\n]*)", review)
    return bool(line) and "쉬움" in line[1] and "어려움" not in line[1]


def write_report(job, task, state, flow):
    """REPORT.md: what the leader tells the owner when the review is done, from the files the stages left."""
    text = lambda name: (job / name).read_text(encoding="utf-8", errors="replace") if (job / name).exists() else ""  # noqa: E731
    url = (text("pr.txt").split() or ["(PR 없음)"])[0]
    review, body = text("review.md"), text("review-body.md")
    # The owner merges the SHA the review names, never one read from repo/ (POLICY §5).
    head = reviewed_head(review)
    counts = re.search(r"고친 것:[^\n]*?P0\s*(\d+)\D+P1\s*(\d+)\D+P2\s*(\d+)", review)
    left = re.search(r"남은 지적:[^\n]*", review)
    door = [l.strip() for l in body.splitlines() if re.match(r"\s*(되돌리기|민감한 파일|Door|신뢰 경계):", l)]
    easy_pr = proven_easy(state) and review_says_easy(job)
    models = "\n".join(f"- {m['stage']}: {m['model']}" + (f" {m['effort']}" if m.get("effort") else "") + (" (haiku 실패 뒤 다시 돌림)" if m["rerun"] else "") for m in state.get("models", []))
    check = text("check.md").split("```")[0].strip()
    setup = text("setup.md").split("```")[0].strip()
    unverified = [l.strip().removeprefix("- ") for l in (review + "\n" + body).splitlines() if "확인하지 않" in l or "미확인" in l]
    report = (f"# {task['name']} 결과\n\n- PR: {url}\n"
              + (f"- 리뷰한 head: `{head}`\n"
                 # The merge queue refuses any other head, so the owner never merges an unreviewed push (POLICY §5).
                 "- 병합(머지 큐에 넣기): `gh api graphql -f query='mutation($id:ID!,$sha:GitObjectID!)"
                 "{enqueuePullRequest(input:{pullRequestId:$id,expectedHeadOid:$sha}){mergeQueueEntry{position}}}' "
                 f"-f id=\"$(gh pr view {url} --json id --jq .id)\" -f sha={head}`\n"
                 "- head가 바뀌어 위 명령이 거절되면 다시 리뷰한다. PR 브랜치에 base를 합치지 않는다\n" if head
                 else "- 리뷰한 head: review.md에서 40자 SHA를 찾지 못함. 병합 명령을 만들지 않는다\n")
              + "- 리뷰가 고친 지적: " + (f"P0 {counts[1]}, P1 {counts[2]}, P2 {counts[3]}" if counts else "review.md에서 찾지 못함") + "\n"
              + f"- {left[0].strip() if left else '남은 지적: review.md에서 찾지 못함'}\n"
              + "".join(f"- {l}\n" for l in door)
              + ("" if easy_pr else "- 되돌리기 어려움이거나 민감한 파일을 건드린다(엔진이 쉬움을 확인하지 못했거나 리뷰가 어려움으로 봤다): 사람이 diff를 읽고 병합한다\n")
              + f"\n## 단계별 모델\n\n{models or '- 없음'}\n\n## 돌린 명령\n\n{setup or '- setup: 없음'}\n\n{check or '- check: 없음'}\n"
              + "\n## 확인하지 않은 것\n\n" + ("".join(f"- {l}\n" for l in unverified) or "- 리뷰 보고와 PR 본문에 적힌 것이 없다\n")
              + f"\n## 리뷰 보고\n\n{review.strip() or '없음'}\n")
    (job / "REPORT.md").write_text(report, encoding="utf-8")


def reviewed_head(review):
    """The 40-hex SHA on the review's 「리뷰한 head」 line, else None."""
    head = re.search(r"리뷰한 head[^\n]*?\b([0-9a-f]{40})\b", review)
    return head[1] if head else None


NOTE_LISTS = ("issues/{n}/comments", "pulls/{n}/comments", "pulls/{n}/reviews")


def pr_notes(job, url, bodies=None):
    """Every comment, line comment and review on the PR as `kind:id:sha256(body)`: a new one or an edit (a bot may post its
    findings by editing its summary) is a new entry. bodies, if given, gets {entry: body}. Raises on any gh failure."""
    number = re.search(r"/pull/(\d+)", url)[1]
    notes = []
    for path in NOTE_LISTS:
        got = subprocess.run(["gh", "api", "--paginate", f"repos/{slug(url)}/{path.format(n=number)}", "--jq",
                              '.[] | [.id, (.body // "")] | @json'], cwd=job, capture_output=True, text=True, timeout=60)
        if got.returncode:
            raise ValueError(f"gh api {path}: {got.stderr.strip()}")
        for line in got.stdout.splitlines():
            ident, body = json.loads(line)
            notes.append(f"{path.split('/')[0]}/{path.split('/')[-1]}:{ident}:{hashlib.sha256(body.encode()).hexdigest()}")
            if bodies is not None:
                bodies[notes[-1]] = body
    return sorted(notes)


def record_seen(name):
    """The review worker runs this before judging: seen.json is the list it judges, and seen-bodies.json the exact bodies
    it judges, so what it read is what the hashes say. enqueue compares the PR's notes with seen.json, so anything new or
    edited afterwards is an unjudged external review."""
    job = JOBS / name
    error = missing(job, name, "pr.txt")
    if error:
        print(error, file=sys.stderr)
        return 2
    try:
        bodies = {}
        notes = pr_notes(job, (job / "pr.txt").read_text(encoding="utf-8").split()[0], bodies)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as failed:
        print(f"could not read the PR's reviews and comments: {failed}", file=sys.stderr)
        return 2
    (job / "seen-bodies.json").write_text(json.dumps(bodies, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (job / "seen.json").write_text(json.dumps(notes, indent=1) + "\n", encoding="utf-8")
    print(f"{len(notes)} reviews and comments recorded in {job / 'seen.json'}; judge the bodies in {job / 'seen-bodies.json'}")
    return 0


def enqueue(job, task, state, passed, flow=()):
    """Put the PR in the merge queue on the reviewed head when nothing needs a person, and say what was done (REPORT.md).
    The owner's standing instruction for the PRs the engine opens (AGENTS.md 「push와 PR」, POLICY §5): the engine proved the
    PR easy, the review says easy with nothing left, CI passed (passed: the head wait_ci saw green, None for no checks) on the
    reviewed head, every review and comment on the PR is one the review worker recorded as judged (seen.json), and the
    approved design is unchanged. Anything else, or any doubt, leaves the merge to a person. The last look at the PR is not
    atomic with the enqueue (GitHub has no conditional enqueue): a comment in that gap is not seen."""
    review = (job / "review.md").read_text(encoding="utf-8", errors="replace") if (job / "review.md").exists() else ""
    url = (job / "pr.txt").read_text(encoding="utf-8").split()[0]
    # Exactly one 「리뷰한 head와 되돌리기」 line, saying <sha>, 쉬움 and nothing else: 쉬움이 아님 or a second line is not easy.
    lines = [l.strip() for l in review.splitlines() if re.search(r"리뷰한\W*head", l)]
    verdict = len(lines) == 1 and re.fullmatch(r"리뷰한 head와 (?:되돌리기|Door): ([0-9a-f]{40}), (?:되돌리기 )?쉬움", lines[0])
    head = verdict[1] if verdict else None
    why = ("엔진이 쉬움을 확인하지 못했다" if not proven_easy(state) else "리뷰가 쉬움으로 보지 않았다" if not verdict
           else "남은 지적이 없음이 아니다" if not claims(review.encode(), r"남은\W*지적[^:]*:", {"남은 지적: 없음"})
           else "리뷰한 head가 CI가 통과한 head와 다르다(체크 없음 포함)" if head != passed else None)
    if why:
        return f"사람이 병합한다: {why}"
    # POLICY §5: the standing instruction never covers the pipeline repository's own PRs (engine.py new --repo <this checkout>)
    # As written and after url.*.insteadOf; neither known is no proof that the PR is elsewhere (fails closed)
    try:
        own = [slug(subprocess.run(["git", "-C", str(OFFICE), *a], capture_output=True, text=True, timeout=10).stdout)
               for a in (["config", "--get", "remote.origin.url"], ["remote", "get-url", "origin"])]
    except (OSError, subprocess.TimeoutExpired):
        own = [None]
    if not slug(url) or None in own or slug(url) in own:
        return "사람이 병합한다: repo-office 자신의 PR이거나 office 체크아웃의 origin을 알 수 없다(상시 지시는 파이프라인 저장소의 PR에 쓰지 않는다)"
    # surrogateescape: a path that is not UTF-8 still reaches the trust check instead of raising
    git = lambda *a: subprocess.run(["git", "-C", str(job / "repo"), *a], capture_output=True, text=True,  # noqa: E731
                                    errors="surrogateescape", timeout=60)
    try:
        judged = json.loads((job / "seen.json").read_text(encoding="utf-8")) if (job / "seen.json").exists() else None
        view = subprocess.run(["gh", "pr", "view", url, "--json", "id,baseRefName,headRefName"], cwd=job,
                              capture_output=True, text=True, timeout=20)
        answer = json.loads(view.stdout) if view.returncode == 0 else None
        if answer["baseRefName"] != task["base"].removeprefix("origin/"):
            return f"사람이 병합한다: PR의 base가 {answer['baseRefName']}로 바뀌었다"
        # pr.txt is a file the workers can write: queue only the branch the engine pushed, in the repository it pushed to
        # As written, and as git fetches and pushes after insteadOf/pushurl: every one must be the PR's repository
        remotes = [git(*a) for a in (["config", "--get", "remote.origin.url"], ["remote", "get-url", "--all", "origin"],
                                     ["remote", "get-url", "--push", "--all", "origin"])]
        if any(r.returncode for r in remotes) or {slug(u) for r in remotes for u in r.stdout.split()} != {slug(url)}:
            return "사람이 병합한다: PR이 엔진이 push한 저장소(작업 트리의 origin)에 있지 않거나 git이 그 저장소를 보여 주지 못했다"
        if answer["headRefName"] != f"office/{task['name']}":
            return f"사람이 병합한다: PR 브랜치가 office/{task['name']}가 아니다({answer['headRefName']})"
        # proven_easy saw the checked commit only: the review's commits may touch a trust path too
        base = git("ls-remote", "origin", f"refs/heads/{answer['baseRefName']}").stdout.split()[:1]
        if not base or git("fetch", "-q", "origin", base[0]).returncode or touches_trust(git, base[0], head):
            return "사람이 병합한다: 리뷰한 head가 민감한 파일(TRUST_NAMES, 제품 REVIEW.md 「민감한 파일」)을 바꾸거나 git이 그 경로를 보여 주지 못했다"
        pr_id = answer["id"]
        unjudged = None if judged is None else set(pr_notes(job, url)) - set(judged)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as error:
        return f"사람이 병합한다: PR을 읽지 못했다({error})"
    if judged is None:
        return "사람이 병합한다: 리뷰 작업자가 판정한 리뷰·댓글 목록(seen.json)이 없다"
    if unjudged:
        return f"사람이 병합한다: 리뷰 작업자가 판정하지 않은 리뷰·댓글이 {len(unjudged)}개 있다(새로 달렸거나 고쳐졌다)"
    # The design may have changed while CI ran and the PR was read: check the approval last, right before the request
    if moved := [s["approve"] for s in flow if s.get("approve") and sha(job / s["approve"]) != state.get("approved", {}).get(s["id"])]:
        return f"사람이 병합한다: 승인 뒤 {moved[0]}이 바뀌었다"
    query = ("mutation($id:ID!,$sha:GitObjectID!){enqueuePullRequest(input:{pullRequestId:$id,expectedHeadOid:$sha})"
             "{mergeQueueEntry{position}}}")
    try:
        put = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}", "-f", f"id={pr_id}", "-f", f"sha={head}"],
                             cwd=job, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"사람이 병합한다: 머지 큐 요청에 답이 없었다({error}). 큐에 들어갔는지 PR에서 확인한다"
    if put.returncode:
        return f"사람이 병합한다: 머지 큐에 넣지 못했다({(put.stderr or put.stdout).strip()})"
    return f"엔진이 `{head}`를 머지 큐에 넣었다(쉬움, 남은 지적 없음, CI 통과). 큐가 최신 main에서 CI를 다시 돌리고 병합한다"


def wait_ci(job, seen=None):
    """Wait for the PR's checks on the head the engine pushed, then note them in REPORT.md; else why not.
    seen["passed"] is that head when its checks passed (not when there were none).
    The engine ends when CI ends, so the leader hears of a red PR from this run instead of finding it on the board."""
    url = (job / "pr.txt").read_text(encoding="utf-8").split()[0]
    head = subprocess.run(["git", "-C", str(job / "repo"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    start = time.time()
    while True:
        try:
            view = subprocess.run(["gh", "pr", "view", url, "--json", "headRefOid,statusCheckRollup"],
                                  cwd=job, capture_output=True, text=True, timeout=20)
            answer = json.loads(view.stdout) if view.returncode == 0 else {}
        except (OSError, subprocess.TimeoutExpired, ValueError):  # gh or the network is down for a moment: ask again
            answer = {}
        waited = time.time() - start
        if isinstance(answer, dict) and answer.get("headRefOid") == head:  # checks of an older head say nothing
            checks = answer.get("statusCheckRollup") if isinstance(answer.get("statusCheckRollup"), list) else []
            ci = ci_state(checks)
            if ci == "fail":
                failed = [f"{c.get('name') or c.get('context')} {c.get('detailsUrl') or c.get('targetUrl') or ''}".strip()
                          for c in checks if ci_state([c]) == "fail"]
                return f"CI failed on {head}: " + "; ".join(failed)
            if ci == "pass" or not ci and waited >= CI_NONE:
                clean = ci and ci_clean(checks)
                if clean and seen is not None:
                    seen["passed"] = head
                with open(job / "REPORT.md", "a", encoding="utf-8") as report:
                    report.write(f"\n## CI\n\n- `{head}`: " + ("통과\n" if clean else "끝남, 통과가 아닌 결론이 있다(예: STALE)\n" if ci
                                                               else f"체크 없음({CI_NONE:g}초 기다림)\n"))
                return None
        elif isinstance(answer, dict) and answer.get("headRefOid") and waited >= CI_MOVED:
            # A push after the engine's, or a local commit never pushed: the reviewed head is stale (POLICY §5).
            return (f"PR head moved: the PR is at {answer['headRefOid']}, not {head} the engine pushed and the review saw. "
                    f"Waiting cannot fix it: a different context reviews the new head (AGENTS.md 「엔진이 멈춘 뒤의 처리」)")
        if waited >= CI_LIMIT:
            return f"CI did not finish on {head} within {CI_LIMIT:g}s (OFFICE_CI_TIMEOUT): a person looks at {url}"
        time.sleep(CI_EVERY)


def missing(job, name, *files):
    """One-line error for the first absent task folder or file, else None."""
    if not job.is_dir():
        return f"no such task: {name}"
    for file in files:
        if not (job / file).exists():
            hint = f": run {name} first" if file == "state.json" else f": {job / file}"
            return f"{file} missing{hint}"
    return None


def run(name, runner=run_worker):
    job = JOBS / name
    error = missing(job, name, "task.json")
    if error:
        print(error, file=sys.stderr)
        return 2
    # Held until run returns or the process dies (kill -9 included). "a" leaves the mtime that workers shows alone.
    with open(job / "run.lock", "a") as lock:
        for wait in (0.05, 0.2, None):  # the board's probe holds a shared lock for a moment: try again before giving up
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if wait is None:
                    print(f"another engine.py run {name} is running", file=sys.stderr)
                    return 2
                time.sleep(wait)
        # A stage can take hours: rewrite the board while this run goes, so minutes, last file and the time stay live.
        # When the run ends or dies the rewrites stop, and the page sees its data grow old.
        stop = threading.Event()

        def keep():
            while not stop.wait(BOARD_EVERY):
                refresh_board()

        keeper = threading.Thread(target=keep, daemon=True)
        keeper.start()
        try:
            return advance(job, name, runner)
        finally:
            stop.set()
            keeper.join()
            fcntl.flock(lock, fcntl.LOCK_UN)  # first, or the last board would still show this run in progress
            refresh_board(ask=False)  # a block or a wait changes what the board shows


def advance(job, name, runner):
    task, config = load(job / "task.json", None), load(STAGES, None)
    flow = config["flow"]
    ids = [stage["id"] for stage in flow]
    state_path = job / "state.json"
    state = load(state_path, {"stage": ids[0], "since": time.time(), "fails": {}, "sessions": []})

    def move(stage_id):
        state.update(stage=stage_id, since=time.time())
        save(state_path, state)
        refresh_board(ask=False)  # the next stage must not wait on gh: the keeper thread asks

    def retry(error):
        """True once per stage for an error a second try may clear."""
        if not any(t in error for t in TRANSIENT) or state.setdefault("retried", {}).get(state["stage"], 0) >= 1:
            return False
        state["retried"][state["stage"]] = 1
        save(state_path, state)
        print(f"retrying once in {RETRY_WAIT:g}s: {error}")
        time.sleep(RETRY_WAIT)
        return True

    def record(reason, run, **extra):
        """Keep every failure, unlike fails: why it failed, when, and the worker run (model, effort) it counts against."""
        state.setdefault("failures", []).append({"stage": state["stage"], "reason": reason, "at": int(time.time()),
                                                 "model": run.get("model"), "effort": run.get("effort"), **extra})

    if state["stage"] == "pr":  # a task saved before the ready stage replaced the person's Ready
        move("ready")
    blocked, intent = job / "BLOCKED.md", job / "INTENT.md"
    # The leader answers a block in INTENT.md's 결정 기록 and reruns: a newer INTENT.md is that answer.
    # Only here, once per run: a worker that blocks and then writes INTENT.md in the same run must not unblock itself.
    if blocked.exists() and intent.exists() and intent.stat().st_mtime > blocked.stat().st_mtime:
        setup = SETUP_BLOCK in blocked.read_text(encoding="utf-8", errors="replace")
        set_aside(blocked)
        state["fails"].clear()
        state.pop("retried", None)  # an answered stage gets its one retry again
        save(state_path, state)
        if setup and (error := run_setup(job, task)):  # an answered setup block runs setup again before anything else
            return block(job, error)

    while True:
        if blocked.exists():
            print(f"blocked: see {blocked}")
            return BLOCKED
        if state["stage"] == "done":
            print("done: see REPORT.md 「병합」: the engine queued an easy PR, else the owner merges it (POLICY §5)")
            return DONE
        index = ids.index(state["stage"])
        # An approval binds the file's content: any later change sends the task back to that approval.
        stale = [s["id"] for s in flow[:index] if s.get("approve") and sha(job / s["approve"]) != state.get("approved", {}).get(s["id"])]
        if stale:
            move(stale[0])
            continue
        stage = flow[index]
        following = ids[index + 1] if index + 1 < len(ids) else "done"
        failed, detail, began = None, {}, int(time.time())
        if stage["by"] == "human":
            if stage.get("approve") and sha(job / stage["approve"]) != state.get("approved", {}).get(stage["id"]):
                file = job / stage["approve"]
                # The owner's standing approval (AGENTS.md 「설계 승인」): an easy design goes on; a hard one waits for a person.
                raw = file.read_bytes() if file.exists() else b""
                # Only the current wording: the old Door: 되돌리기 쉬움 still reads as easy for a PR, but never approves a design.
                if stage.get("auto") != "easy" or not claims(raw, DOOR_CLAIM, {"되돌리기: 쉬움"}):
                    print(f"waiting: a person reads {file} and runs engine.py approve {name}")
                    return WAIT
                # The sha256 of the bytes just judged easy: a file swapped since fails the binding check and comes back here.
                state.setdefault("approved", {})[stage["id"]] = state.setdefault("auto_approved", {})[stage["id"]] = hashlib.sha256(raw).hexdigest()
                state.pop("easy_pr", None)  # as approve(): an open PR is never proven again
                save(state_path, state)
                print(f"auto-approved {stage['approve']} {state['approved'][stage['id']]}: it says 되돌리기: 쉬움")
        elif stage["by"] == "script" and stage.get("body"):
            # Push only the commit the check stage passed: a commit made since (a rerun after a block) goes through it again.
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=job / "repo", capture_output=True, text=True).stdout.strip()
            if not (job / stage["out"]).exists() and head != state.get("checked"):
                check = next((s["id"] for s in reversed(flow[:index]) if s["by"] == "script" and not s.get("body")), None)
                if not check:
                    return block(job, "no check stage before the draft stage: push only a tested commit")
                move(check)
                continue
            raw = read_body(job / stage["body"])
            # The commit compared above, not HEAD again: HEAD can move between here and the push.
            approved = next((state.get("approved", {}).get(s["id"]) for s in flow if stage.get("easy") and s.get("approve") == stage["easy"]), None)
            seen = {}
            if error := open_draft(job, task, stage, raw, state["checked"], approved, seen):
                if retry(error):
                    continue
                return block(job, error)
            if seen:  # the review runs on sonnet only for a PR proven easy here; a PR opened elsewhere gets opus
                state["easy_pr"] = state["checked"]
                save(state_path, state)
        elif stage["by"] == "script" and stage.get("ready"):
            if error := make_ready(job, stage, state):
                if retry(error):
                    continue
                return block(job, error)
            save(state_path, state)
        elif stage["by"] == "script" and stage.get("edit"):
            if error := post_body(job, task, stage):
                if retry(error):
                    continue
                return block(job, error)
            # The review raised the revert verdict to hard: the bot the ready stage skipped reviews the head just pushed.
            if not review_says_easy(job):
                request = next((s.get("request") for s in flow if s.get("ready")), None)
                url = (job / "pr.txt").read_text(encoding="utf-8").split()[0]
                if error := ask_bot(job, url, request, state):
                    if retry(error):
                        continue
                    return block(job, error)
                save(state_path, state)
            write_report(job, task, state, flow)
        elif stage["by"] == "script" and stage.get("ci"):
            seen = {}
            if error := wait_ci(job, seen):  # a red CI needs a person: no retry, the leader passes it on
                return block(job, error)
            if stage.get("enqueue") == "easy":
                note = enqueue(job, task, state, seen.get("passed"), flow)
                with open(job / "REPORT.md", "a", encoding="utf-8") as report:
                    report.write(f"\n## 병합\n\n- {note}\n")
        elif stage["by"] == "script":
            git = lambda *a: subprocess.run(["git", *a], cwd=job / "repo", capture_output=True, text=True).stdout.strip()  # noqa: E731
            # Read what the worker handed over before the test writes its own files (caches, reports).
            head, dirty = git("rev-parse", "HEAD"), git("status", "--porcelain", "--untracked-files=all")
            result = run_test(job, task, capture_output=True, text=True)
            (job / stage["out"]).write_text(
                f"# {stage['id']}\n\n- 명령: `{task['test']}`\n- 커밋: `{head}`\n- exit {result.returncode}\n"
                f"- 커밋하지 않은 변경: {dirty or '없음'}\n\n```\n{(result.stdout + result.stderr)[-4000:]}\n```\n", encoding="utf-8")
            if result.returncode or dirty:
                failed = f"`{task['test']}` exit {result.returncode}" + (", uncommitted changes" if dirty else "")
                # The next check rewrites check.md: the full output of this failed one stays beside it, outside repo/.
                log = f"test.fail-{sum(f['stage'] == stage['id'] for f in state.get('failures', [])) + 1}.log"
                (job / log).write_text(result.stdout + result.stderr, encoding="utf-8")
                detail = {"exit": result.returncode, "log": log}
            else:
                state.update(checked=head)  # saved by move() below
        else:
            if stage["id"] == "review" and (reason := unpinned(job, task)):
                return block(job, reason)
            out = job / stage["out"]
            out.unlink(missing_ok=True)
            # A body left from an earlier try must not be posted as this try's.
            if following != "done" and (edit := flow[index + 1].get("edit")):
                (job / edit).unlink(missing_ok=True)
            prompt = job / f"{stage['id']}.prompt.md"
            prompt.write_text(fill((PIPELINE / stage["prompt"]).read_text(encoding="utf-8"), job, task), encoding="utf-8")
            # The task folder sits inside the trusted office checkout; a fresh worktree would show the trust dialog.
            argv = claude_argv(config, stage, job, task, state)
            model = argv[argv.index("--model") + 1]
            effort = argv[argv.index("--effort") + 1] if "--effort" in argv else None
            # A restarted worker re-reads both files: the leader may have answered a block in INTENT.md since its last turn.
            again = any(m["stage"] == stage["id"] for m in state.get("models", [])) or "--resume" in argv
            message = (f"This stage runs again. Re-read {job / 'INTENT.md'} (its 결정 기록 may hold new answers) and {prompt}, "
                       f"then follow {prompt} exactly." if again else f"Read {prompt} and follow it exactly.")
            state["working"] = model  # the board names the model of the worker now running, before models records it
            save(state_path, state)
            start = int(time.time())
            error = runner(f"{TAG}-{name}-{stage['id']}"[:32], job, argv, message)
            if not isinstance(error, NotStarted):
                if session_key(stage, state) not in state["sessions"]:
                    state["sessions"].append(session_key(stage, state))
                state.setdefault("models", []).append({"stage": stage["id"], "model": model, "effort": effort,
                                                       "rerun": stage["id"] in state.get("escalated", []),
                                                       "start": start, "end": int(time.time())})
                save(state_path, state)  # a blocked stage returns below; the next run must still --resume
            if error:
                # A block, not a failed try (usage limit, timeout, hc): "blocked" keeps it apart from the stage's own failures.
                record(str(error), {"model": model, "effort": effort}, blocked=True)
                save(state_path, state)
                if retry(error):
                    continue
                return block(job, error)
            if (job / "BLOCKED.md").exists():
                reason = next((l.strip() for l in (job / "BLOCKED.md").read_text(encoding="utf-8", errors="replace").splitlines()
                               if l.strip() and not l.startswith("#")), "BLOCKED.md")
                # A haiku worker that gives up is a failed try (sonnet reruns it), not a block.
                record(reason, {"model": model, "effort": effort}, **({} if model == "haiku" else {"blocked": True}))
                save(state_path, state)
                if model == "haiku":  # a haiku worker that gave up: sonnet tries once in a new session
                    (job / "BLOCKED.md").replace(job / "BLOCKED.haiku.md")
                    state.setdefault("escalated", []).append(stage["id"])
                    save(state_path, state)
                continue
            if not out.exists():
                failed = f"{stage['id']} ended without writing {out.name}"
        if stage["by"] == "script":  # a script stage that returns early (a block, a wait, a retry) leaves no timing
            state.setdefault("timings", []).append({"stage": stage["id"], "start": began, "end": int(time.time())})
        if failed:
            # A failed haiku run (no result file, or a failed check of its commit): sonnet reruns it once, in a new session.
            # Any other failed worker reruns at one effort level higher (stage_effort).
            target = stage.get("retry", stage["id"])
            ran = [m["model"] for m in state.get("models", []) if m["stage"] == target]
            # A failed check counts against the implement run whose commit it checked.
            record(failed, next((m for m in reversed(state.get("models", [])) if m["stage"] == target), {}), **detail)
            if ran[-1:] == ["haiku"]:
                if target not in state.get("escalated", []):
                    state.setdefault("escalated", []).append(target)
            elif ran and target not in state.get("raised", []):
                state.setdefault("raised", []).append(target)
            state["fails"][stage["id"]] = state["fails"].get(stage["id"], 0) + 1
            save(state_path, state)
            if state["fails"][stage["id"]] >= 2:
                state["fails"].clear()  # an answer in INTENT.md gets two fresh tries
                save(state_path, state)
                return block(job, f"{failed}, twice in a row")
            move(stage.get("retry", stage["id"]))
            continue
        state["fails"].pop(stage["id"], None)
        state.get("retried", {}).pop(stage["id"], None)
        move(following)


def run_test(job, task, **kwargs):
    """The owner wrote this command (the product's AGENTS.md test cell) with `new`."""
    return subprocess.run(task["test"], shell=True, cwd=job / "repo", **kwargs)


def test(name):
    """The worker's Bash output is cut in the middle, which lost the summary line: the full output goes to test.log."""
    job = JOBS / name
    error = missing(job, name, "task.json")
    if error:
        print(error, file=sys.stderr)
        return 2
    log = job / "test.log"
    with open(log, "w+b") as out:
        code = run_test(job, load(job / "task.json", None), stdout=out, stderr=subprocess.STDOUT).returncode
        out.seek(0)
        text = out.read().decode("utf-8", errors="replace")
    print(text[-4000:])
    print(f"exit {code}; full output: {log}")
    return code


def preflight(name):
    """review_preflight.py reads the git repository of its working folder; a worker's folder is inside the office checkout."""
    job = JOBS / name
    error = missing(job, name, "task.json", "pr.txt")
    if error:
        print(error, file=sys.stderr)
        return 2
    pr = (job / "pr.txt").read_text(encoding="utf-8").split()  # the draft stage or a person wrote it
    script = BASELINE / "tools/review_preflight.py"
    diff_file = job / "pr.diff"
    diff_file.unlink(missing_ok=True)
    result = subprocess.run([sys.executable, str(script), *pr[:1], "--json"], cwd=job / "repo", capture_output=True,
                            text=True, errors="replace")
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode:  # 1 means a secrets file changed: the review must not open the diff
        return result.returncode
    # The engine runs the diff and hands over the text, built from base and head checked here, with .env* left out.
    try:
        found = json.loads(result.stdout)
        base, head = found["base"], found["head"]
        ok = (not found["secret_hits"] and isinstance(found["diff_argv"], list) and isinstance(base, str)
              and isinstance(head, str) and re.fullmatch(r"refs/remotes/[\w./-]+", base) and re.fullmatch(r"[0-9a-f]{40}", head))
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        print("the preflight output is not the expected JSON", file=sys.stderr)
        return 2
    argv = ["git", "-C", str(job / "repo"), "diff", "--ignore-submodules=none", f"{base}...{head}", "--", ".", ":!.env*", ":!**/.env*"]
    # Bytes, not text: a non-UTF-8 file in the PR would raise, and an uncaught error exits 1, the secrets-file code.
    diff = subprocess.run(argv, capture_output=True)
    if diff.returncode:
        print(f"diff failed: {diff.stderr.decode(errors='replace').strip()}", file=sys.stderr)
        return 2
    diff_file.write_bytes(diff.stdout)
    print(f"diff 본문: {diff_file}")
    return 0


def skills(name, base):
    """matt-skills check_skills.py, like preflight, reads the git repository of its working folder."""
    job = JOBS / name
    error = missing(job, name, "task.json")
    # The base ref preflight printed, nothing else: no options, no `..` revision ranges.
    if error or not re.fullmatch(r"refs/remotes/[\w/-]+(\.[\w-]+)*", base) or "//" in base:
        print(error or f"base must be the preflight's base ref, got {base!r}", file=sys.stderr)
        return 2
    script = BASELINE / "profiles/matt-skills/check_skills.py"
    # Workers load this checkout's project settings, which turn the plugin on even when the user's settings turn it off.
    # A repo-baseline clone older than --settings refuses the option (exit 2): the review then goes on without delegating.
    return subprocess.run([sys.executable, "-I", str(script), base, "--harness", "claude",
                           "--settings", str(OFFICE / ".claude/settings.json")], cwd=job / "repo").returncode


def approve(name):
    job = JOBS / name
    error = missing(job, name, "task.json", "state.json")
    if error:
        print(error, file=sys.stderr)
        return 2
    state_path = job / "state.json"
    state = load(state_path, None)
    stage = next(s for s in load(STAGES, None)["flow"] if s["id"] == state["stage"])
    if not stage.get("approve") or not (job / stage["approve"]).exists():
        print(f"nothing to approve at stage {state['stage']}", file=sys.stderr)
        return 2
    state.setdefault("approved", {})[stage["id"]] = sha(job / stage["approve"])
    state.get("auto_approved", {}).pop(stage["id"], None)  # a person approved these bytes
    state.pop("easy_pr", None)  # proven for the design approved before; an open PR is never proven again
    save(state_path, state)
    print(f"approved {stage['approve']} {state['approved'][stage['id']]}")
    return 0


def commands(text):
    """The setup, check and test cells of an AGENTS.md 「명령」 section (POLICY §2), None for N/A, else why not."""
    section = re.search(r"^## 명령[ \t]*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not section:
        return "AGENTS.md has no `## 명령` section"
    cells = {}
    for name in ("setup", "check", "test"):
        found = re.search(rf"^- {name}: `([^`]+)`", section[1], re.M)
        if not found:
            return f"AGENTS.md 「명령」 has no `- {name}: `<command>`` line"
        cells[name] = None if found[1].startswith("N/A") else found[1]
    if not cells["test"]:
        return "the AGENTS.md test cell is N/A: the pipeline needs a test command"
    return cells


def new(name, repo, base):
    # The name becomes a folder, a branch and an hc terminal name (a-z 0-9 - _ only).
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,23}", name):
        print("task name: a-z 0-9 - _, at most 24 characters", file=sys.stderr)
        return 2
    if base.startswith("origin/"):  # read the commands and branch from what origin has now, not a stale local ref
        subprocess.run(["git", "-C", repo, "fetch", "-q", "origin", base.removeprefix("origin/")], capture_output=True)
    shown = subprocess.run(["git", "-C", repo, "show", f"{base}:AGENTS.md"], capture_output=True, text=True)
    cells = commands(shown.stdout) if shown.returncode == 0 else f"no AGENTS.md at {base}"
    if isinstance(cells, str):
        print(f"{cells}: run engine.py doctor {repo}", file=sys.stderr)
        return 2
    job = JOBS / name
    job.mkdir(parents=True)
    # Each cell in its own subshell: a test cell like `cd app; make test` must not hide a failing check.
    # The newlines keep a trailing `# comment` in a cell from swallowing the closing parenthesis.
    test = " && ".join(f"(\n{c}\n)" for c in (cells["check"], cells["test"]) if c)
    save(job / "task.json", {"name": name, "repo": str(Path(repo).resolve()), "test": test, "setup": cells["setup"], "base": base})
    (job / "INTENT.md").write_text(
        f"# {name}\n\n## 목표\n\n## 지시 원문\n\n## 하지 않을 것\n\n## 결정 기록\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "worktree", "add", "-b", f"office/{name}", str(job / "repo"), base], check=True)
    if error := run_setup(job, load(job / "task.json", None)):
        return block(job, error)
    print(job / "INTENT.md")
    return 0


SETUP_BLOCK = "setup has not passed in repo/"


def run_setup(job, task):
    """Run the setup cell once in repo/, else why not. BLOCKED.md stands while it runs: an interrupted setup stays blocked."""
    if not task.get("setup"):
        return None
    (job / "BLOCKED.md").write_text(f"# 막힘\n\n{SETUP_BLOCK}: `{task['setup']}` is running or was interrupted\n", encoding="utf-8")  # not block(): no "blocked:" line while setup runs
    done = subprocess.run(task["setup"], shell=True, cwd=job / "repo", capture_output=True, text=True)
    (job / "setup.md").write_text(f"# setup\n\n- 명령: `{task['setup']}`\n- exit {done.returncode}\n\n```\n"
                                  f"{(done.stdout + done.stderr)[-4000:]}\n```\n", encoding="utf-8")
    if done.returncode:
        return (f"{SETUP_BLOCK}: `{task['setup']}` exit {done.returncode}. Fix the product or the machine, write the answer "
                f"in INTENT.md's 결정 기록, and run again: the engine runs setup again first. Output: {job / 'setup.md'}")
    (job / "BLOCKED.md").unlink()
    return None


def doctor(repo):
    """What a product repository lacks to connect, checked on origin/main. Exit 0 when nothing is missing.
    Every fix is in the product repository or this machine: the pipeline does not bend to one product."""
    repo, missing_ = Path(repo).resolve(), []

    def report(ok, what, fix=""):
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else f": {fix}"))
        if not ok:
            missing_.append(what)

    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)  # noqa: E731
    report(git("fetch", "-q", "origin", "main").returncode == 0, "origin has a main branch", "push main to origin")
    shown = git("show", "origin/main:AGENTS.md")
    cells = commands(shown.stdout) if shown.returncode == 0 else "no AGENTS.md on origin/main"
    report(not isinstance(cells, str), "AGENTS.md 「명령」 has setup, check and test cells",
           f"{cells} (repo-baseline templates/core/AGENTS.md)")
    report(git("cat-file", "-e", "origin/main:.github/pull_request_template.md").returncode == 0, "PR template on main",
           "add .github/pull_request_template.md (repo-baseline templates/core/pull_request_template.md)")
    authed = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
    report(authed, "gh is logged in", "run gh auth login")
    repo_slug = slug(git("config", "--get", "remote.origin.url").stdout)  # as written, like the draft stage
    protected = False
    if repo_slug and authed:
        api = lambda path, *a: subprocess.run(["gh", "api", path, *a], capture_output=True, text=True)  # noqa: E731
        rules = api(f"repos/{repo_slug}/rules/branches/main", "--jq", "length")
        protected = api(f"repos/{repo_slug}/branches/main/protection").returncode == 0 or (
            rules.returncode == 0 and rules.stdout.strip() not in ("", "0"))
    report(protected, "main is protected (branch protection or a ruleset)",
           "turn it on (repo-baseline tools/apply_github_settings.py)" if repo_slug else "origin is not a GitHub repository")
    root = Path(tempfile.mkdtemp(prefix="office-doctor-"))
    checkout = root / "main"
    try:
        added = git("worktree", "add", "-q", "--detach", str(checkout), "origin/main")
        report(added.returncode == 0, "a fresh checkout of origin/main", added.stderr.strip())
        contract(checkout, repo_slug, report)
        # setup first: check and test may need what it installs
        for name in () if added.returncode or isinstance(cells, str) else ("setup", "check", "test"):
            if cells[name] is None:
                print(f"ok   {name}: N/A")
                continue
            done = subprocess.run(cells[name], shell=True, cwd=checkout, capture_output=True, text=True)
            report(done.returncode == 0, f"{name} passes on main: `{cells[name]}`",
                   f"exit {done.returncode}; make it pass on a fresh checkout of main\n{(done.stdout + done.stderr)[-1500:]}")
    finally:
        git("worktree", "remove", "--force", str(checkout))
        shutil.rmtree(root, ignore_errors=True)
    return 1 if missing_ else 0


def contract(checkout, repo_slug, report):
    """repo-baseline's GitHub contract for origin/main (checkout): its tool plans (never --apply) and says what differs.
    Exit 1 is a difference; anything else is a plan that could not read the settings (no access, no network, no
    settings file, no checkout): never a pass. The tool checks the repository the settings file names, so a file
    copied from another repository must not pass for this one."""
    what = "GitHub settings match the repo-baseline contract (apply_github_settings.py plan)"
    settings = checkout / ".github/repository-settings.json"
    try:
        named = str(json.loads(settings.read_text(encoding="utf-8")).get("repository") or "")
    except (OSError, ValueError, AttributeError):  # no file or not an object: the tool says why
        named = None
    if named is not None and repo_slug and named.lower() != repo_slug:
        return report(False, what, f".github/repository-settings.json on main names {named!r}, but origin is {repo_slug}")
    try:
        planned = subprocess.run([sys.executable, str(BASELINE / "tools/apply_github_settings.py"), str(checkout)],
                                 cwd=checkout if checkout.is_dir() else None, capture_output=True, text=True, timeout=300)
        code, out, err = planned.returncode, planned.stdout, planned.stderr
    except (OSError, subprocess.TimeoutExpired) as error:
        code, out, err = None, "", f"{type(error).__name__}: {error}"
    # A crash (an import error) also exits 1: a difference is only one the plan printed.
    if code == 1 and any(l.startswith(("DIFF:", "MANUAL:", "PLAN:")) for l in out.splitlines()):
        return report(False, what, "the owner applies it as repo-baseline README says\n"
                      + "\n".join(l for l in out.splitlines() if l.startswith(("DIFF:", "MANUAL:"))))
    report(code == 0, what, "확인 못 함 (unverified): " + ((err or out).strip().splitlines() or [f"exit {code}"])[-1])


CI_FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def ci_state(checks):
    """The PR's checks (gh's statusCheckRollup) in one word: "fail" if any failed, else "pending" if any has not
    finished, else "pass"; "" with no checks. A failure wins: a finished failure stays one while other checks run."""
    words = [str(c.get("state") or "") if c.get("__typename") == "StatusContext"
             else "PENDING" if c.get("status") != "COMPLETED" else str(c.get("conclusion") or "")
             for c in (checks if isinstance(checks, list) else []) if isinstance(c, dict)]
    if any(w in CI_FAILED for w in words):
        return "fail"
    if any(w in ("PENDING", "EXPECTED") for w in words):
        return "pending"
    return "pass" if words else ""


def ci_clean(checks):
    """Every check finished with a conclusion that is a pass (SUCCESS, SKIPPED, NEUTRAL): STALE or anything new is not."""
    words = [str(c.get("state") or "") if c.get("__typename") == "StatusContext" else str(c.get("conclusion") or "")
             for c in (checks if isinstance(checks, list) else []) if isinstance(c, dict)]
    return bool(words) and all(w in ("SUCCESS", "SKIPPED", "NEUTRAL") for w in words)


def pr_state(job, state, ask=True):
    """(merged, merge, ci) for a task's open PR, from the stage after the PR is opened: merged is True once a finished
    task's PR is merged, merge is GitHub's mergeStateStatus while it is open (CLEAN, BEHIND, DIRTY, BLOCKED, UNSTABLE,
    ...; "" if unknown), ci is ci_state of its checks. A person merges and CI runs outside the engine, so no file says
    it: gh is asked at most every MERGE_EVERY seconds a task, the last answer is kept in .merge-asked, and a merged PR
    is kept in .merged and never asked again. ask=False only reads the last answer: a caller that must not wait on gh."""
    if (job / ".merged").exists():
        clear_queue_block(job)  # also a task merged before the engine cleared the note itself
        return True, "", ""
    pr, asked, done = job / "pr.txt", job / ".merge-asked", state.get("stage") == "done"
    if state.get("stage") not in ("ready", "review", "post", "ci", "done") or not pr.exists():
        return False, "", ""
    try:
        last = load(asked, {})
    except (OSError, ValueError):  # an empty or half-written mark: as if never asked
        last = {}
    if not isinstance(last, dict):
        last = {}
    kept = (False, str(last.get("merge", "")), str(last.get("ci", "")))
    if not ask or asked.exists() and time.time() - asked.stat().st_mtime < MERGE_EVERY:
        return kept
    try:
        view = subprocess.run(["gh", "pr", "view", pr.read_text(encoding="utf-8").strip(), "--json",
                               "state,mergeStateStatus,statusCheckRollup"], cwd=job, capture_output=True, text=True, timeout=20)
        answer = json.loads(view.stdout) if view.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):  # no gh or no network: keep the last answer
        answer = {}
    if not isinstance(answer, dict) or not answer:
        asked.touch()
        return kept
    if done and answer.get("state") == "MERGED":  # a PR merged mid-review stays on the board until the engine finishes
        (job / ".merged").touch()
        clear_queue_block(job)
        return True, "", ""
    merge = str(answer.get("mergeStateStatus") or "") if answer.get("state") == "OPEN" else str(answer.get("state") or "")
    ci = ci_state(answer.get("statusCheckRollup")) if answer.get("state") == "OPEN" else ""
    save(asked, {"merge": merge, "ci": ci})
    if done and answer.get("state") == "OPEN":
        queue_removed(job, pr.read_text(encoding="utf-8").strip())
    return False, merge, ci


QUEUE_QUERY = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){state isInMergeQueue "
               "timelineItems(last:1,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT]){nodes{__typename "
               "... on RemovedFromMergeQueueEvent{id createdAt reason}}}}}}")


QUEUE_BLOCK = "removed from merge queue at "


def set_aside(blocked):
    """Move an answered BLOCKED.md to BLOCKED.<mtime>.md, or BLOCKED.<mtime>.<n>.md when a note from the same second is kept.
    The note leaves the name BLOCKED.md first, in one rename to a name only this thread uses: a block() written after
    that makes a new BLOCKED.md and never shares the kept note's file. os.link then fails on a name that exists, so a
    note another process sets aside at the same moment is never overwritten (without hard links, see rename_aside).
    A BLOCKED.md already gone was set aside by another mover."""
    held = blocked.with_name(f".BLOCKED.{os.getpid()}.{threading.get_ident()}.md")  # dot: workers and the board skip it
    try:
        os.rename(blocked, held)
    except FileNotFoundError:
        return
    stamp, n = int(held.stat().st_mtime), 0
    while True:
        try:
            os.link(held, held.with_name(f"BLOCKED.{stamp}{f'.{n}' if n else ''}.md"))
            break
        except FileExistsError:
            n += 1
        except OSError as e:
            try:
                if e.errno not in NO_LINKS:
                    raise
                rename_aside(held, stamp)
                return
            except OSError:  # no archive (disk full): the note goes back to BLOCKED.md unless a new block took it
                put_back(held, blocked, stamp)
                raise
    held.unlink()


NO_LINKS = {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV}  # os.link on a file system without hard links


def rename_aside(held, stamp):
    """Without hard links (exFAT, some network mounts): rename the held note onto the first free BLOCKED.<stamp>[.<n>].md.
    shortcut: a note another process keeps between the check and the rename is overwritten (the gap before os.link);
    only file systems without hard links take this path."""
    n = 0
    while (kept := held.with_name(f"BLOCKED.{stamp}{f'.{n}' if n else ''}.md")).exists():
        n += 1
    os.rename(held, kept)


def put_back(held, blocked, stamp):
    """Return a held note that could not be archived to BLOCKED.md. os.link never overwrites a new BLOCKED.md. Without
    hard links the note is kept beside a new BLOCKED.md, or renamed back after a check (see the shortcut below).
    If nothing works, the note stays held."""
    try:
        try:
            os.link(held, blocked)
            held.unlink()
        except OSError as e:
            if e.errno not in NO_LINKS:
                raise
            if blocked.exists():
                rename_aside(held, stamp)
            else:
                os.rename(held, blocked)  # shortcut: a block() written between the check and the rename is overwritten
    except OSError:
        pass


def clear_queue_block(job):
    """The PR merged, or a person put it back in the queue: queue_removed's BLOCKED.md no longer holds. It is moved aside
    like an answered block (BLOCKED.<mtime>.md). Any other block stays: only a person's answer clears those."""
    blocked = job / "BLOCKED.md"
    try:
        line = next((l for l in blocked.read_text(encoding="utf-8", errors="replace").splitlines()
                     if l.strip() and not l.startswith("#")), "")
        if line.startswith(QUEUE_BLOCK):
            set_aside(blocked)
            return True
    except OSError:  # no BLOCKED.md, or another reader moved it first
        pass
    return False


def queue_removed(job, url):
    """A person or the ci stage put the finished task's PR in the merge queue and it left unmerged (merge_group CI failed, a conflict, a
    person dequeued it): write why into BLOCKED.md so workers and the board show it. Each removal event once (.queue-removed).
    It only reads: the engine queues a PR once (the ci stage) and never re-enqueues or merges. No answer from gh is no alert."""
    found = re.search(r"/pull/(\d+)", url)
    repo_slug = slug(url)
    if not found or not repo_slug:
        return
    owner, name = repo_slug.split("/")
    try:
        # -f keeps owner and name strings (-F would turn a repo named 2048 or true into a number or a bool); n is an Int.
        view = subprocess.run(["gh", "api", "graphql", "-f", f"query={QUEUE_QUERY}", "-f", f"o={owner}", "-f", f"r={name}",
                               "-F", f"n={found[1]}"], cwd=job, capture_output=True, text=True, timeout=20)
        pull = json.loads(view.stdout)["data"]["repository"]["pullRequest"] if view.returncode == 0 else None
        last = pull["timelineItems"]["nodes"][-1]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError, TypeError):
        return
    if pull.get("state") == "OPEN" and pull.get("isInMergeQueue") is True:  # a person fixed it and put it back
        # After the move: a stale in-queue answer that moved a newer note aside lets the next look write it again.
        # No move, no reset: the same event must not overwrite another block.
        if clear_queue_block(job):
            (job / ".queue-removed").unlink(missing_ok=True)
        return
    # GitHub logs a merge as a removal too (reason "merged"), sometimes before the PR reads MERGED.
    if (not isinstance(last, dict) or last.get("__typename") != "RemovedFromMergeQueueEvent" or pull.get("state") != "OPEN"
            or pull.get("isInMergeQueue") is not False or str(last.get("reason") or "").lower() == "merged"):
        return
    seen = job / ".queue-removed"
    if seen.exists() and seen.read_text(encoding="utf-8").strip() == str(last.get("id")):
        return
    (job / "BLOCKED.md").write_text(
        f"# 막힘\n\n{QUEUE_BLOCK}{last.get('createdAt')}: {last.get('reason') or 'no reason given'} {url}\n\n"
        "The engine does not enqueue it again. A person fixes the cause; a new push needs a new review (POLICY §5).\n",
        encoding="utf-8")
    seen.write_text(f"{last.get('id')}\n", encoding="utf-8")


def job_status(job, ask=True):
    """What workers and the board show for one task, from its files, and for a finished one whether its PR is merged."""
    state = load(job / "state.json", {"stage": "-", "since": time.time()})
    done, merge, ci = pr_state(job, state, ask)  # first: it may write BLOCKED.md (queue_removed), shown in this same row
    blocked = job / "BLOCKED.md"
    files = []
    for p in job.iterdir():
        try:  # a worker may delete a file between the listing and its stat
            if p.is_file() and p.name != "run.lock" and not p.name.startswith("."):
                files.append((p.stat().st_mtime, p.name))
        except FileNotFoundError:
            pass
    when, last = max(files)
    if blocked.exists():
        note = next((l.strip() for l in blocked.read_text(encoding="utf-8", errors="replace").splitlines()
                     if l.strip() and not l.startswith("#")), "")[:60]  # block() writes a 「# 막힘」 heading first
    elif (job / "pr.txt").exists():
        note = (job / "pr.txt").read_text(encoding="utf-8").strip()
    else:  # fails counts tries since the last pass or answer: while it does, the last failure says why a stage runs again
        fail = (state.get("failures") or [None])[-1]
        # A haiku give-up (BLOCKED.md) adds no fail, but sonnet reruns the same stage without a move: show it until the
        # stage moves on. at is whole seconds, since is not: a give-up in the second the stage began still counts.
        again = isinstance(fail, dict) and (state.get("fails") or (
            fail.get("model") == "haiku" and not fail.get("blocked") and fail.get("stage") == state.get("stage")
            and fail.get("at", -1) >= int(state["since"])))
        note = f"직전 실패: {fail.get('reason', '')}"[:60] if again else ""
    # run() holds run.lock while it works (this process too, on another descriptor): a lock we cannot share means an engine runs.
    running = 0
    if (job / "run.lock").exists():
        with open(job / "run.lock") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
            except BlockingIOError:
                running = 1
    models = state.get("models") or [{"model": "-"}]
    return {"name": job.name, "stage": "blocked" if blocked.exists() else "merged" if done else state["stage"], "at": state["stage"],
            "merged": int(done), "merge": merge, "ci": ci,
            "minutes": int((time.time() - state["since"]) // 60),
            "model": state.get("working") or models[-1]["model"],
            # Every stage run, oldest first, as "stage=model" words; "+" marks the sonnet rerun after haiku gave up.
            "ran": " ".join(f"{m.get('stage', '-')}={m.get('model', '-')}{'+' if m.get('rerun') else ''}"
                            for m in state.get("models", [])),
            "running": running, "worker": f"{TAG}-{job.name}-{state['stage']}"[:32],  # the hc name run_worker gives
            "last": f"{last} {datetime.fromtimestamp(when):%H:%M}", "note": note}


def tasks(ask=True):
    rows = []
    for job in sorted(p for p in JOBS.glob("*") if (p / "task.json").exists()):
        try:
            # state.json may hold any JSON (NaN, objects): give the views only text and whole numbers
            rows.append({k: v if type(v) is int else str(v) for k, v in job_status(job, ask).items()})  # not isinstance: bool must become text
        except Exception as error:  # one hand-edited state.json (since not a number, a key missing) must not hide the rest
            rows.append({"name": job.name, "stage": "unreadable", "at": "-", "minutes": 0, "model": "-", "last": "-",
                         "ran": "", "running": 0, "worker": "", "merged": 0, "merge": "", "ci": "", "note": f"state.json을 읽지 못함: {type(error).__name__}"})
    return rows


def workers():
    print(f"{'과제':<24} {'단계':<10} {'단계 경과':>9}  {'모델':<7} 마지막 파일 · PR 또는 막힌 이유")
    for t in tasks():
        print(f"{t['name']:<24} {t['stage']:<10} {t['minutes']:>7}분  {t['model']:<7} {t['last']}"
              + (f" · {t['note']}" if t["note"] else ""))
    return 0


def board(ask=True):
    """Write jobs/board.html, one screen: a pixel office with one character per task, beside jobs/board-tasks.html,
    stage lanes and a card per task from files only, which reloads itself every 5 seconds and hands its rows to the office.
    run() rewrites both every BOARD_EVERY seconds while it goes; "stamp" lets the page tell data no engine rewrites."""
    flow = [{"id": s["id"], "by": s["by"]} for s in load(STAGES, {"flow": []})["flow"]]
    data = json.dumps({"time": f"{datetime.now():%H:%M:%S}", "stamp": int(time.time()), "flow": flow, "tasks": tasks(ask)}, ensure_ascii=False)
    # Inside <script>, a worker's text must not close the tag: escape <, > and & as JSON \u escapes.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    # The office's images go inline as data URIs: the page loads nothing from anywhere else.
    art = {p.stem: "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode() for p in sorted((PIPELINE / "office").glob("*.png"))}
    pages = {"board-tasks.html": (PIPELINE / "board-tasks.html").read_text(encoding="utf-8").replace("/*DATA*/", data),
             "board.html": (PIPELINE / "board.html").read_text(encoding="utf-8").replace("/*ART*/", json.dumps(art))}
    JOBS.mkdir(parents=True, exist_ok=True)
    for name, page in pages.items():
        tmp = JOBS / f".{name}.{os.getpid()}.{threading.get_ident()}.tmp"  # other runs and this run's keeper write too
        tmp.write_text(page, encoding="utf-8")
        tmp.replace(JOBS / name)  # a reload never reads half a page
    return JOBS / "board.html"


def refresh_board(ask=True):
    """The board is a view: failing to write it must not stop a task. It reads every task's files, so a malformed
    state.json of another task (TypeError, AttributeError) must not replace this run's exit code either. ask=False
    keeps gh out (pr_state): the stage loop calls it that way, so a stalled network never holds up the next stage."""
    try:
        board(ask)
    except Exception as error:
        print(f"board not written: {type(error).__name__}: {error}", file=sys.stderr)


def watch_board(rounds=None):
    """Rewrite the board every BOARD_EVERY seconds until stopped, so it stays current while no engine runs: a person
    merges, the base moves, a task waits. One watcher a jobs folder: a second one exits at once. rounds is for tests."""
    JOBS.mkdir(parents=True, exist_ok=True)
    with open(JOBS / ".board-watch.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another board --watch already rewrites this board", file=sys.stderr)
            return 0
        print(JOBS / "board.html", flush=True)
        n = 0
        try:
            while rounds is None or n < rounds:
                refresh_board()
                n += 1
                if rounds is None or n < rounds:
                    time.sleep(BOARD_EVERY)
        except KeyboardInterrupt:
            pass
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    created = sub.add_parser("new")
    created.add_argument("task")
    created.add_argument("--repo", required=True)
    created.add_argument("--base", default="origin/main")
    sub.add_parser("doctor").add_argument("repo")
    for command in ("run", "approve", "test", "preflight", "seen"):
        sub.add_parser(command).add_argument("task")
    checked = sub.add_parser("skills")
    checked.add_argument("task")
    checked.add_argument("base")
    sub.add_parser("workers")
    sub.add_parser("board").add_argument("--watch", action="store_true", help="keep rewriting the board until stopped")
    args = parser.parse_args(argv)
    if BASELINE is None and args.command not in ("workers", "board"):
        print(f"write the absolute path of the repo-baseline clone into {_PATH}", file=sys.stderr)
        return 2
    if args.command == "new":
        return new(args.task, args.repo, args.base)
    if args.command == "doctor":
        return doctor(args.repo)
    if args.command == "workers":
        return workers()
    if args.command == "board":
        if args.watch:
            return watch_board()
        print(board())
        return 0
    if args.command == "skills":
        return skills(args.task, args.base)
    return {"run": run, "approve": approve, "test": test, "preflight": preflight, "seen": record_seen}[args.command](args.task)


if __name__ == "__main__":
    sys.exit(main())
