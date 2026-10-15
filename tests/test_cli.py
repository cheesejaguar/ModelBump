import json

import pytest

from modelbump.cli import main
from modelbump.config import parse_config
from modelbump.editor import StaleFileError, apply_changes
from modelbump.planner import build_plan
from modelbump.scanner import scan

APP = '''import anthropic

client = anthropic.Anthropic()
MODEL = "claude-opus-5"
FALLBACK = "anthropic/claude-sonnet-4.5"  # via OpenRouter
LOCKED = "claude-opus-5"  # modelbump: ignore
'''


def run(capsys, *args):
    code = main([*args, "--offline", "--today", "2026-10-15"])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def project(repo):
    (repo / "app.py").write_text(APP)
    return repo


def test_scan(project, capsys):
    code, out, _ = run(capsys, "scan", "-C", str(project))
    assert code == 0
    assert "2 model reference(s) in 1 file(s)" in out
    assert "app.py:4" in out and "Claude Opus 5" in out


def test_plan_json(project, capsys):
    code, out, _ = run(capsys, "plan", "-C", str(project), "--json")
    data = json.loads(out)
    assert code == 0
    assert [(c["from"], c["to"], c["kind"]) for c in data["changes"]] == [
        ("claude-opus-5", "claude-opus-5-5", "minor"),
        ("anthropic/claude-sonnet-4.5", "anthropic/claude-sonnet-5.5", "major"),
    ]


def test_plan_fail_on_updates(project, capsys):
    code, out, _ = run(capsys, "plan", "-C", str(project), "--fail-on-updates", "--diff")
    assert code == 2
    assert '-MODEL = "claude-opus-5"' in out and '+MODEL = "claude-opus-5-5"' in out


def test_apply_rewrites_only_the_slugs(project, capsys):
    code, out, _ = run(capsys, "apply", "-C", str(project))
    assert code == 0
    assert (project / "app.py").read_text() == APP.replace('MODEL = "claude-opus-5"', 'MODEL = "claude-opus-5-5"').replace(
        "anthropic/claude-sonnet-4.5", "anthropic/claude-sonnet-5.5"
    )
    code, out, _ = run(capsys, "plan", "-C", str(project))
    assert "0 update(s) proposed" in out


def test_apply_preserves_crlf(repo, catalog, today):
    (repo / "a.py").write_bytes(b'A = "claude-opus-5"\r\nB = 1\r\n')
    config = parse_config({})
    plan = build_plan(scan(repo, catalog, config), catalog, config, today)
    apply_changes(repo, plan.changes)
    assert (repo / "a.py").read_bytes() == b'A = "claude-opus-5-5"\r\nB = 1\r\n'


def test_apply_refuses_stale_offsets(repo, catalog, today):
    (repo / "a.py").write_text('A = "claude-opus-5"\n')
    config = parse_config({})
    plan = build_plan(scan(repo, catalog, config), catalog, config, today)
    (repo / "a.py").write_text('# moved\nA = "claude-opus-5"\n')
    with pytest.raises(StaleFileError):
        apply_changes(repo, plan.changes)


def test_strategy_flag_and_config_errors(project, capsys):
    (project / ".github").mkdir()
    (project / ".github" / "modelbump.yml").write_text("version: 1\nstrategy: sideways\n")
    code, _, err = run(capsys, "plan", "-C", str(project))
    assert code == 1 and "strategy" in err


def test_catalog_search(project, capsys):
    code, out, _ = run(capsys, "catalog", "-C", str(project), "--search", "opus-5")
    assert code == 0
    assert "Claude Opus 5.5" in out and "bundled snapshot" in out


def test_init_writes_starter_config(tmp_path, capsys):
    assert main(["init", "-C", str(tmp_path)]) == 0
    assert (tmp_path / ".github" / "modelbump.yml").exists()
    assert main(["init", "-C", str(tmp_path)]) == 1
