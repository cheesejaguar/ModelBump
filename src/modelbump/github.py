"""Open Dependabot-style pull requests with git and the GitHub CLI (``gh``).

One branch per update (or one for everything with ``grouping: single``).
A branch is rebuilt from the base branch and pushed only when its edits
changed; an existing PR then gets its title and body refreshed, and an open PR
for an older target of the same model is closed as superseded. Branches other
people committed to, and updates whose PR was closed, are left alone.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog
from .config import Config
from .editor import apply_changes
from .planner import Group, Plan
from .report import pr_body

log = logging.getLogger("modelbump")
MAX_BODY = 60_000  # GitHub rejects bodies over 65,536 characters


class GitHubError(RuntimeError):
    pass


@dataclass
class PullRequestResult:
    branch: str
    title: str
    action: str  # created | updated | unchanged | skipped | would-create | would-update
    url: str | None = None
    detail: str | None = None


NETWORK_TIMEOUT = 180  # seconds, for commands that talk to GitHub


def _run(cmd: list[str], cwd: Path, *, input: str | None = None, timeout: float | None = None) -> str:
    network = cmd[0] != "git" or cmd[1] in ("push", "fetch", "ls-remote")
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            input=input,
            stdin=None if input is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            # Never wait for a password prompt: fail with a message instead.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"},
            timeout=timeout or (NETWORK_TIMEOUT if network else None),
        )
    except FileNotFoundError as e:
        raise GitHubError(f"{cmd[0]} is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise GitHubError(
            f"`{' '.join(cmd[:3])}` did not finish within {e.timeout:.0f}s; check that git and gh can reach "
            "GitHub without prompting (credentials, SSH agent approval)"
        ) from e
    if proc.returncode != 0:
        raise GitHubError(f"`{' '.join(cmd[:3])}` failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _gh() -> str:
    return os.environ.get("MODELBUMP_GH", "gh")


def _commit_message(group: Group, config: Config, path_prefix: str = "") -> str:
    prefix = config.pull_requests.commit_message_prefix.strip()
    title = group.title()
    lines = [f"{prefix} {title[0].lower()}{title[1:]}" if prefix else title, ""]
    for c in group.changes:
        lines.append(
            f"- {path_prefix}{c.occurrence.path}:{c.occurrence.line}: {c.from_slug.text} -> {c.to_slug.text}"
        )
    lines += ["", f"{COMMIT_TRAILER}: {group.from_model.key} -> {group.to_model.key}"]
    return "\n".join(lines) + "\n"


def _ensure_labels(root: Path, labels: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Create missing labels. Returns (labels to apply, labels that couldn't be
    created). Creating labels needs the token's ``issues: write`` permission;
    passing a label that doesn't exist to ``gh pr create`` would fail the PR."""
    if not labels:
        return [], []
    try:
        existing = {l["name"] for l in json.loads(_run([_gh(), "label", "list", "--json", "name", "--limit", "500"], root))}
    except (GitHubError, ValueError) as e:
        log.warning("could not list labels: %s", e)
        return [], list(labels)
    usable, missing = [], []
    for label in labels:
        if label in existing:
            usable.append(label)
            continue
        try:
            _run([_gh(), "label", "create", label, "--description", "Model slug updates by ModelBump"], root)
            usable.append(label)
        except GitHubError as e:
            log.warning("could not create label %s: %s", label, e)
            missing.append(label)
    return usable, missing


COMMIT_TRAILER = "ModelBump-Update"


@dataclass
class _RemoteBranch:
    tip: str
    patch_id: str | None  # patch-id of the branch's change against its merge-base
    foreign: bool  # someone else committed to the branch


def _patch_id(root: Path, a: str, b: str) -> str:
    diff = _run(["git", "diff", a, b], root)
    out = _run(["git", "patch-id", "--stable"], root, input=diff) if diff.strip() else ""
    return out.split()[0] if out.strip() else ""


def _remote_branches(root: Path, prefix: str) -> dict[str, str]:
    """All ModelBump branches on origin (name -> tip), fetched in one go."""
    out = _run(["git", "ls-remote", "--heads", "origin", f"refs/heads/{prefix}/*"], root)
    tips = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            tips[ref[len("refs/heads/") :]] = sha
    if tips:
        _run(["git", "fetch", "-q", "--no-tags", "origin", *(f"refs/heads/{b}" for b in tips)], root)
    return tips


def _remote_branch(root: Path, tip: str | None, base_sha: str) -> _RemoteBranch | None:
    if tip is None:
        return None
    try:
        merge_base = _run(["git", "merge-base", base_sha, tip], root).strip()
    except GitHubError:
        return _RemoteBranch(tip, None, foreign=False)
    commits = _run(["git", "rev-list", f"{merge_base}..{tip}"], root).split()
    message = _run(["git", "log", "-1", "--format=%B", tip], root)
    foreign = len(commits) != 1 or f"{COMMIT_TRAILER}:" not in message
    return _RemoteBranch(tip, _patch_id(root, merge_base, tip), foreign)


def open_pull_requests(
    plan: Plan,
    root: Path,
    catalog: Catalog,
    config: Config,
    today: dt.date,
    *,
    base: str | None = None,
    dry_run: bool = False,
) -> list[PullRequestResult]:
    prefs = config.pull_requests

    if _run(["git", "status", "--porcelain", "--untracked-files=no"], root).strip():
        raise GitHubError("the working tree has uncommitted changes; commit or stash them first")
    # Only edit tracked files, so a PR never sweeps in untracked local files.
    tracked = set(_run(["git", "ls-files", "-z"], root).split("\0"))
    skipped = sorted({c.occurrence.path for c in plan.changes if c.occurrence.path not in tracked})
    if skipped:
        log.warning("not opening PRs for untracked files: %s", ", ".join(skipped))
    plan = Plan(plan.occurrences, [c for c in plan.changes if c.occurrence.path in tracked], plan.findings, plan.today)
    groups = plan.groups(prefs.grouping)
    if not groups:
        return []

    start = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root).strip()
    base = base or prefs.base or start
    head_sha = _run(["git", "rev-parse", "HEAD"], root).strip()
    base_sha = _run(["git", "rev-parse", base], root).strip()
    if head_sha != base_sha:
        raise GitHubError(f"run ModelBump with {base!r} checked out (HEAD is {start!r}) so edits apply cleanly")

    prs = json.loads(
        _run(
            [_gh(), "pr", "list", "--state", "all", "--json", "number,headRefName,url,state", "--limit", "1000"],
            root,
        )
    )
    remote_tips = {} if dry_run else _remote_branches(root, prefs.branch_prefix)
    path_prefix = _run(["git", "rev-parse", "--show-prefix"], root).strip()
    mine = [p for p in prs if p["headRefName"].startswith(prefs.branch_prefix + "/")]
    ours = {p["headRefName"]: p for p in mine if p["state"].upper() == "OPEN"}
    closed = {p["headRefName"]: p for p in mine if p["state"].upper() == "CLOSED"}
    open_count = len(ours)
    labels, missing_labels = (list(prefs.labels), []) if dry_run else _ensure_labels(root, prefs.labels)
    label_note = (
        f"labels {', '.join(missing_labels)} not applied; give the workflow `issues: write` so ModelBump can "
        "create them"
        if missing_labels
        else None
    )

    results: list[PullRequestResult] = []
    try:
        for g in groups:
            branch = g.branch(prefs.branch_prefix)
            title = g.title()
            existing = ours.get(branch)
            if existing is None and branch in closed:
                # Like Dependabot: a closed PR means "not this update".
                results.append(
                    PullRequestResult(
                        branch, title, "skipped", closed[branch]["url"],
                        detail="closed previously; reopen it to resume this update",
                    )
                )  # fmt: skip
                continue
            if existing is None and open_count >= prefs.open_pull_requests_limit:
                results.append(
                    PullRequestResult(branch, title, "skipped", detail="open-pull-requests-limit reached")
                )
                continue
            if dry_run:
                action = "would-update" if existing else "would-create"
                results.append(PullRequestResult(branch, title, action, existing["url"] if existing else None))
                continue

            remote = _remote_branch(root, remote_tips.get(branch), base_sha)
            if remote and remote.foreign:
                # Like Dependabot: once someone else pushes to the branch, hands off.
                results.append(
                    PullRequestResult(
                        branch, title, "skipped", existing["url"] if existing else None,
                        detail="branch has commits not made by ModelBump; leaving it alone",
                    )
                )  # fmt: skip
                continue
            _run(["git", "checkout", "-q", "-B", branch, base_sha], root)
            files = apply_changes(root, g.changes)
            _run(["git", "add", "--", *files], root)
            _run(["git", "commit", "-q", "-F", "-"], root, input=_commit_message(g, config, path_prefix))
            # Same edits as the pushed branch (even if the base moved elsewhere):
            # don't force-push, so reviewers and CI aren't disturbed. Edits whose
            # context changed on the base produce a different patch and rebase.
            unchanged = remote is not None and remote.patch_id == _patch_id(root, base_sha, "HEAD")
            if existing and unchanged:
                results.append(PullRequestResult(branch, title, "unchanged", existing["url"]))
                continue
            if not unchanged:
                lease = f"refs/heads/{branch}:{remote.tip if remote else ''}"
                _run(["git", "push", "-q", f"--force-with-lease={lease}", "origin", f"{branch}:{branch}"], root)

            body = pr_body(g, catalog, config, today, path_prefix=path_prefix)
            if len(body) > MAX_BODY:
                body = body[:MAX_BODY] + "\n\n…(truncated)"
            if existing:
                _run([_gh(), "pr", "edit", str(existing["number"]), "--title", title, "--body-file", "-"], root, input=body)
                result = PullRequestResult(branch, title, "updated", existing["url"])
            else:
                cmd = [_gh(), "pr", "create", "--base", base, "--head", branch, "--title", title, "--body-file", "-"]
                for label in labels:
                    cmd += ["--label", label]
                if prefs.reviewers:
                    cmd += ["--reviewer", ",".join(prefs.reviewers)]
                if prefs.assignees:
                    cmd += ["--assignee", ",".join(prefs.assignees)]
                try:
                    url = _run(cmd, root, input=body).strip().splitlines()[-1]
                except GitHubError as e:
                    if "not permitted to create or approve pull requests" in str(e):
                        raise GitHubError(
                            "GitHub Actions may not open pull requests in this repository. Enable Settings -> "
                            "Actions -> General -> 'Allow GitHub Actions to create and approve pull requests', "
                            "or pass a GitHub App / personal access token as the action's github-token."
                        ) from e
                    raise
                result = PullRequestResult(branch, title, "created", url, detail=label_note)
                open_count += 1
            results.append(result)

            stem = g.branch_stem(prefs.branch_prefix)
            for other_branch, other in ours.items():
                if other_branch != branch and other_branch.startswith(stem):
                    _run(
                        [
                            _gh(), "pr", "close", str(other["number"]), "--delete-branch",
                            "--comment", f"Superseded by {result.url or branch}.",
                        ],
                        root,
                    )  # fmt: skip
                    open_count -= 1
            ours = {b: p for b, p in ours.items() if b == branch or not b.startswith(stem)}
    finally:
        # Only ModelBump's own edits can be uncommitted here (the tree was clean
        # at the start), so forcing the checkout cannot lose user work.
        _run(["git", "checkout", "-q", "-f", start], root)
    return results
