import pytest
import yaml
from importlib import resources

from modelbump.config import ConfigError, glob_match, load_config, parse_config


def test_defaults():
    c = parse_config(None)
    (rule,) = c.rules
    assert rule.strategy == "same-family"
    assert rule.same_family.update_types == ("snapshot", "minor", "major")
    assert rule.cost_optimized.scope == "ecosystem"
    assert c.ecosystem_of("qwen") == c.ecosystem_of("deepseek") == "chinese-open"
    assert c.ecosystem_of("anthropic") == "anthropic"


def test_bundled_starter_config_is_valid():
    text = resources.files("modelbump").joinpath("data/default_config.yml").read_text()
    c = parse_config(yaml.safe_load(text))
    assert c.pull_requests.labels == ("dependencies", "llm-models")


def test_rule_overrides_merge_over_top_level_settings():
    c = parse_config(
        {
            "cost-optimized": {"min-savings": 0.3},
            "updates": [{"paths": ["jobs/**"], "strategy": "cost-optimized", "cost-optimized": {"prefer": "best-score"}}],
        }
    )
    (rule,) = c.rules
    assert rule.cost_optimized.min_savings == 0.3
    assert rule.cost_optimized.prefer == "best-score"
    assert c.rule_for("jobs/a.py") is rule
    assert c.rule_for("api/a.py") is None  # no rule matches -> not scanned


def test_custom_ecosystems_move_vendors():
    c = parse_config({"ecosystems": {"frontier": ["anthropic", "openai"]}})
    assert c.ecosystem_of("openai") == "frontier"
    assert "openai" not in c.ecosystems.get("openai", [])


@pytest.mark.parametrize(
    "raw,message",
    [
        ({"strategy": "fastest"}, "strategy"),
        ({"same-family": {"update-types": ["patch"]}}, "update-types"),
        ({"cost-optimized": {"min-savings": 2}}, "min-savings"),
        ({"cost-optimized": {"bogus": 1}}, "unknown key"),
        ({"ecosystems": {"x": ["not-a-vendor"]}}, "unknown vendor"),
        ({"ignore": [{}]}, "needs at least one"),
        ({"version": 2}, "version"),
        ({"catalog": {"sources": ["hf"]}}, "catalog.sources"),
    ],
)
def test_validation_errors(raw, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_snake_case_keys_are_accepted():
    c = parse_config({"same_family": {"update_types": ["minor"]}})
    assert c.rules[0].same_family.update_types == ("minor",)


@pytest.mark.parametrize(
    "path,pattern,ok",
    [
        ("src/a.py", "src/**", True),
        ("src/x/y/a.py", "src/**/*.py", True),
        ("a.py", "**/*.py", True),
        ("deep/dir/CHANGELOG.md", "CHANGELOG*", True),
        ("deep/dir/CHANGELOG.md", "**/CHANGELOG*", True),
        ("src/a.py", "tests/**", False),
        ("src/a.py", "*.ts", False),
    ],
)
def test_glob_match(path, pattern, ok):
    assert glob_match(path, pattern) is ok


def test_load_config_finds_github_dir(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "modelbump.yml").write_text("version: 1\nstrategy: cost-optimized\n")
    c = load_config(tmp_path)
    assert c.rules[0].strategy == "cost-optimized"
    assert c.path == tmp_path / ".github" / "modelbump.yml"


def test_load_config_reports_file_on_error(tmp_path):
    (tmp_path / ".modelbump.yml").write_text("strategy: nope\n")
    with pytest.raises(ConfigError, match=r"\.modelbump\.yml"):
        load_config(tmp_path)
