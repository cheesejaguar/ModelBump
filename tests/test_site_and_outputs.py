import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from modelbump.cli import main
from modelbump.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def run(capsys, *args):
    code = main([*args, "--offline", "--today", "2026-10-15"])
    return code, capsys.readouterr().out


@pytest.fixture
def sample(tmp_path):
    dest = tmp_path / "sample-app"
    shutil.copytree(ROOT / "examples" / "sample-app", dest)
    return dest


def test_sample_app_plan(sample, capsys):
    code, out = run(capsys, "plan", "-C", str(sample), "--json")
    changes = {(c["path"], c["from"], c["to"]) for c in json.loads(out)["changes"]}
    assert ("app/agent.py", "claude-opus-5", "claude-opus-5-5") in changes
    assert ("web/router.ts", "anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5.5") in changes
    assert (
        "config/models.yaml",
        "anthropic.claude-sonnet-4-20250514-v1:0",
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
    ) in changes
    assert ("config/models.yaml", "gpt-4o-2024-08-06", "gpt-4o-2024-11-20") in changes


def test_summary_report_and_annotations(sample, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    report = tmp_path / "report.json"
    code, out = run(
        capsys, "plan", "-C", str(sample), "--summary", str(summary), "--report-json", str(report),
        "--github-annotations",
    )  # fmt: skip
    assert code == 0
    assert "::notice file=app/agent.py,line=6,title=ModelBump::claude-opus-5 can be bumped to claude-opus-5-5" in out
    md = summary.read_text()
    assert md.startswith("## ModelBump")
    assert "| Bump Claude Opus 5 to Claude Opus 5.5 | minor | 1 | `app/agent.py` |" in md
    data = json.loads(report.read_text())
    assert len(data["changes"]) >= 5


def test_annotation_values_are_escaped(tmp_path, capsys):
    (tmp_path / "a,b.py").write_text('M = "claude-opus-5"\n')
    _, out = run(capsys, "plan", "-C", str(tmp_path), "--github-annotations")
    assert "::notice file=a%2Cb.py,line=1" in out


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")
@pytest.mark.parametrize(
    "args,strategy,updates,grouping",
    [
        ([], "same-family", ("snapshot", "minor", "major"), "per-model"),
        (
            ["--strategy", "cost-optimized", "--updates", "minor,major", "--grouping", "single", "--prerelease"],
            "cost-optimized",
            ("minor", "major"),
            "single",
        ),
    ],
)
def test_install_script(tmp_path, args, strategy, updates, grouping):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    proc = subprocess.run(
        ["bash", str(ROOT / "site" / "install.sh"), "--yes", "--no-settings", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    workflow = yaml.safe_load((tmp_path / ".github/workflows/modelbump.yml").read_text())
    assert workflow["permissions"] == {"contents": "write", "pull-requests": "write", "issues": "write"}
    step = workflow["jobs"]["modelbump"]["steps"][1]
    assert step["uses"] == "cheesejaguar/ModelBump@v0"
    config = load_config(tmp_path)
    assert config.rules[0].strategy == strategy
    assert config.rules[0].same_family.update_types == updates
    assert config.pull_requests.grouping == grouping


def test_install_script_rejects_bad_options(tmp_path):
    proc = subprocess.run(
        ["bash", str(ROOT / "site" / "install.sh"), "--updates", "patch"], cwd=tmp_path, capture_output=True, text=True
    )
    assert proc.returncode == 2 and "--updates" in proc.stderr


def test_site_build(tmp_path):
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
    build_site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_site)
    out = tmp_path / "site"
    shutil.copytree(ROOT / "site", out)
    build_site.fingerprint(out)
    data = build_site.export(out, offline=True)

    html = (out / "index.html").read_text()
    assert 'href="styles.css?v=' in html and 'src="app.js?v=' in html
    assert data["slugs"]["claude-opus-5"]["to"] == "claude-opus-5-5"
    assert data["slugs"]["gpt-3.5-turbo"].get("why", "").startswith("GPT-4 Turbo costs")
    assert ["claude-opus-5", "claude-opus-5-5"] in data["hero"]
    assert json.loads((out / "data" / "catalog.json").read_text())["stats"]["models"] > 100
