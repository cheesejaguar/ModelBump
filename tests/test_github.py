import json
import sys
import textwrap

import pytest

from conftest import git
from modelbump.config import parse_config
from modelbump.github import GitHubError, open_pull_requests
from modelbump.planner import build_plan
from modelbump.scanner import scan

FAKE_GH = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys
    state_path = os.environ["FAKE_GH_STATE"]
    state = json.load(open(state_path))
    args = sys.argv[1:]
    state["calls"].append(args)
    out = ""
    if args[:2] == ["pr", "list"]:
        out = json.dumps(state["prs"])
    elif args[:2] == ["label", "list"]:
        out = json.dumps([{{"name": "dependencies"}}])
    elif args[:2] == ["pr", "create"]:
        n = 100 + len(state["prs"])
        head = args[args.index("--head") + 1]
        state["prs"].append({{"number": n, "headRefName": head, "url": f"https://gh/pr/{{n}}",
                             "state": "OPEN", "body": sys.stdin.read(),
                             "title": args[args.index("--title") + 1]}})
        out = f"https://gh/pr/{{n}}"
    elif args[:2] == ["pr", "edit"]:
        for p in state["prs"]:
            if p["number"] == int(args[2]):
                p["body"] = sys.stdin.read()
    elif args[:2] == ["pr", "close"]:
        for p in state["prs"]:
            if p["number"] == int(args[2]):
                p["state"] = "CLOSED"
    json.dump(state, open(state_path, "w"))
    print(out)
    """
)


@pytest.fixture
def gh(tmp_path, monkeypatch):
    script = tmp_path / "gh"
    script.write_text(FAKE_GH.format(python=sys.executable))
    script.chmod(0o755)
    state = tmp_path / "gh_state.json"
    state.write_text(json.dumps({"prs": [], "calls": []}))
    monkeypatch.setenv("MODELBUMP_GH", str(script))
    monkeypatch.setenv("FAKE_GH_STATE", str(state))

    class Handle:
        def read(self):
            return json.loads(state.read_text())

        def add_pr(self, number, branch, pr_state="OPEN"):
            s = self.read()
            s["prs"].append({"number": number, "headRefName": branch, "url": f"https://gh/pr/{number}", "state": pr_state})
            state.write_text(json.dumps(s))

        def calls(self, *prefix):
            return [c for c in self.read()["calls"] if c[: len(prefix)] == list(prefix)]

    return Handle()


@pytest.fixture
def project(repo, tmp_path):
    (repo / "app.py").write_text('OPUS = "claude-opus-5"\nGPT = "gpt-4o-2024-08-06"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    return repo


def run_prs(project, catalog, today, config=None, **kw):
    config = config or parse_config({})
    plan = build_plan(scan(project, catalog, config), catalog, config, today)
    return open_pull_requests(plan, project, catalog, config, today, **kw)


def test_creates_one_pr_per_update(project, catalog, today, gh):
    results = run_prs(project, catalog, today)
    assert [(r.action, r.branch) for r in results] == [
        ("created", "modelbump/anthropic/claude-opus-5-to-claude-opus-5.5"),
        ("created", "modelbump/openai/gpt-4o-2024-08-06-to-gpt-4o-2024-11-20"),
    ]
    # each branch carries only its own edit
    opus = git(project, "show", "origin/modelbump/anthropic/claude-opus-5-to-claude-opus-5.5:app.py")
    assert opus == 'OPUS = "claude-opus-5-5"\nGPT = "gpt-4o-2024-08-06"\n'
    log = git(project, "log", "-1", "--format=%B", "origin/modelbump/anthropic/claude-opus-5-to-claude-opus-5.5")
    assert log.startswith("chore(models): bump Claude Opus 5 to Claude Opus 5.5")
    # back on main with a clean tree
    assert git(project, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert git(project, "status", "--porcelain") == ""

    state = gh.read()
    body = state["prs"][0]["body"]
    assert "Bumps **Claude Opus 5** to **Claude Opus 5.5**" in body
    assert "| `app.py:1` | `claude-opus-5` | `claude-opus-5-5` | first-party API |" in body
    assert "\\$4.00 / \\$20.00" in body  # escaped so GitHub doesn't render math
    assert ["label", "create", "llm-models", "--description", "Model slug updates by ModelBump"] in state["calls"]


def test_rerun_without_changes_leaves_prs_alone(project, catalog, today, gh, monkeypatch):
    run_prs(project, catalog, today)
    pushes = []
    import modelbump.github as mg

    real_run = mg._run

    def spy(cmd, cwd, **kw):
        if cmd[:2] == ["git", "push"]:
            pushes.append(cmd)
        return real_run(cmd, cwd, **kw)

    monkeypatch.setattr(mg, "_run", spy)
    results = run_prs(project, catalog, today)
    assert [r.action for r in results] == ["unchanged", "unchanged"]
    assert pushes == []
    assert gh.calls("pr", "edit") == []


def test_rerun_with_new_references_updates_the_pr(project, catalog, today, gh):
    run_prs(project, catalog, today)
    (project / "worker.py").write_text('M = "anthropic/claude-opus-5"\n')
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "worker")
    results = run_prs(project, catalog, today)
    # the base moved, but only the Opus update has new edits
    assert [r.action for r in results] == ["updated", "unchanged"]
    files = git(project, "show", "--name-only", "--format=", "origin/modelbump/anthropic/claude-opus-5-to-claude-opus-5.5")
    assert set(files.split()) == {"app.py", "worker.py"}


def test_branch_edited_by_a_human_is_left_alone(project, catalog, today, gh, tmp_path):
    run_prs(project, catalog, today)
    branch = "modelbump/anthropic/claude-opus-5-to-claude-opus-5.5"
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "-b", branch, str(project / ".." / "remote.git"), str(clone))
    (clone / "app.py").write_text('OPUS = "claude-opus-5-5"  # tuned prompt\nGPT = "gpt-4o-2024-08-06"\n')
    git(clone, "-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-qam", "tune")
    git(clone, "push", "-q", "origin", branch)
    (project / "worker.py").write_text('M = "claude-opus-5"\n')
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "worker")
    results = run_prs(project, catalog, today)
    assert results[0].action == "skipped" and "not made by ModelBump" in results[0].detail
    assert "tuned prompt" in git(project, "show", f"origin/{branch}:app.py")


def test_closed_pr_is_not_reopened(project, catalog, today, gh):
    gh.add_pr(3, "modelbump/anthropic/claude-opus-5-to-claude-opus-5.5", "CLOSED")
    results = run_prs(project, catalog, today)
    assert [(r.action, r.detail) for r in results][0] == (
        "skipped",
        "closed previously; reopen it to resume this update",
    )
    assert results[1].action == "created"


def test_untracked_files_are_left_out(project, catalog, today, gh):
    (project / "scratch.py").write_text('M = "claude-opus-5"\n')
    results = run_prs(project, catalog, today)
    assert results[0].action == "created"
    files = git(project, "show", "--name-only", "--format=", "origin/modelbump/anthropic/claude-opus-5-to-claude-opus-5.5")
    assert files.split() == ["app.py"]
    assert (project / "scratch.py").exists()


def test_superseded_pr_is_closed(project, catalog, today, gh):
    gh.add_pr(7, "modelbump/anthropic/claude-opus-5-to-claude-opus-5.2")
    run_prs(project, catalog, today)
    old = next(p for p in gh.read()["prs"] if p["number"] == 7)
    assert old["state"] == "CLOSED"


def test_open_pull_request_limit(project, catalog, today, gh):
    config = parse_config({"pull-requests": {"open-pull-requests-limit": 1}})
    results = run_prs(project, catalog, today, config)
    assert [r.action for r in results] == ["created", "skipped"]


def test_dry_run_touches_nothing(project, catalog, today, gh):
    results = run_prs(project, catalog, today, dry_run=True)
    assert [r.action for r in results] == ["would-create", "would-create"]
    assert gh.read()["prs"] == []
    assert "modelbump" not in git(project, "branch", "-a")


def test_refuses_dirty_tree(project, catalog, today, gh):
    (project / "app.py").write_text('OPUS = "claude-opus-5"\n# wip\n')
    with pytest.raises(GitHubError, match="uncommitted"):
        run_prs(project, catalog, today)


def test_subdirectory_paths_are_repo_relative(project, catalog, today, gh):
    (project / "svc").mkdir()
    (project / "svc" / "api.py").write_text('M = "claude-sonnet-5"\n')
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "svc")
    git(project, "push", "-q", "origin", "main")
    config = parse_config({})
    sub = project / "svc"
    plan = build_plan(scan(sub, catalog, config), catalog, config, today)
    (result,) = open_pull_requests(plan, sub, catalog, config, today)
    body = gh.read()["prs"][0]["body"]
    assert "| `svc/api.py:1` |" in body
    log = git(project, "log", "-1", "--format=%B", f"origin/{result.branch}")
    assert "- svc/api.py:1: claude-sonnet-5 -> claude-sonnet-5-5" in log
