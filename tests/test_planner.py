import pytest

from modelbump.catalog import apply_scores
from modelbump.config import parse_config
from modelbump.planner import build_plan
from modelbump.scanner import Matcher, scan_text


def plan_for(catalog, today, text, config=None, path="app.py"):
    config = config or parse_config({})
    occ = scan_text(path, text, Matcher(catalog.all_slugs()))
    return build_plan(occ, catalog, config, today)


def bumps(plan):
    return [(c.from_slug.text, c.to_slug.text, c.kind) for c in plan.changes]


# --- same-family -----------------------------------------------------------------


def test_opus_minor_bump(catalog, today):
    assert bumps(plan_for(catalog, today, '"claude-opus-5"')) == [("claude-opus-5", "claude-opus-5-5", "minor")]


def test_stays_in_the_same_tier(catalog, today):
    plan = plan_for(catalog, today, '"claude-haiku-4-5" "claude-sonnet-4-6" "gpt-5-mini"')
    assert bumps(plan) == [
        ("claude-haiku-4-5", "claude-haiku-5-5", "major"),
        ("claude-sonnet-4-6", "claude-sonnet-5-5", "major"),
        ("gpt-5-mini", "gpt-5.4-mini", "minor"),
    ]


def test_update_types_limit_the_bump(catalog, today):
    config = parse_config({"same-family": {"update-types": ["minor"]}})
    plan = plan_for(catalog, today, '"claude-opus-4-5"', config)
    assert bumps(plan) == [("claude-opus-4-5", "claude-opus-4-8", "minor")]


def test_already_latest_is_left_alone(catalog, today):
    assert plan_for(catalog, today, '"claude-opus-5-5"').changes == []


def test_release_date_beats_version_number(catalog, today):
    # grok-4.20 shipped before grok-4.3 ... 4.7
    assert bumps(plan_for(catalog, today, '"x-ai/grok-4.20"')) == [("x-ai/grok-4.20", "x-ai/grok-4.7", "minor")]
    assert plan_for(catalog, today, '"x-ai/grok-4.7"').changes == []


def test_prerelease_needs_opt_in(catalog, today):
    plan = plan_for(catalog, today, '"google/gemini-2.5-pro"')
    assert plan.changes == []
    assert "prerelease successor available" in plan.findings[0].message
    config = parse_config({"same-family": {"allow-prerelease": True}})
    plan = plan_for(catalog, today, '"google/gemini-2.5-pro"', config)
    assert bumps(plan) == [("google/gemini-2.5-pro", "google/gemini-3.1-pro-preview", "major")]


def test_snapshot_bump_for_pinned_ids(catalog, today):
    assert bumps(plan_for(catalog, today, '"gpt-4o-2024-08-06"')) == [
        ("gpt-4o-2024-08-06", "gpt-4o-2024-11-20", "snapshot")
    ]
    config = parse_config({"same-family": {"update-types": ["minor", "major"]}})
    assert plan_for(catalog, today, '"gpt-4o-2024-08-06"', config).changes == []


# --- format preservation ---------------------------------------------------------


@pytest.mark.parametrize(
    "src,dst",
    [
        ("anthropic/claude-opus-4.5", "anthropic/claude-opus-5.5"),  # OpenRouter dots
        ("anthropic/claude-sonnet-4.5:batch", "anthropic/claude-sonnet-5.5:batch"),  # variant kept
        ("anthropic.claude-sonnet-5", "anthropic.claude-sonnet-5-5"),  # Bedrock Mantle
        # versioned Bedrock ids only move to versioned Bedrock ids
        ("anthropic.claude-sonnet-4-20250514-v1:0", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
        ("claude-opus-4-5@20251101", "claude-opus-5-5"),  # Vertex: current models use bare ids
        ("claude-3-5-sonnet-20241022", "claude-sonnet-5-5"),  # legacy naming, retired
        ("deepseek-ai/DeepSeek-V4-Flash", "deepseek-ai/DeepSeek-V4.1-Flash"),  # Hugging Face id
    ],
)
def test_target_is_written_in_the_original_format(catalog, today, src, dst):
    assert [c.to_slug.text for c in plan_for(catalog, today, f'"{src}"').changes] == [dst]


def test_pinned_to_floating_is_explained(catalog, today):
    change = plan_for(catalog, today, '"claude-sonnet-4-5-20250929"').changes[0]
    assert change.to_slug.text == "claude-sonnet-5-5"
    assert any("no dated" in n for n in change.notes)


def test_free_variant_without_successor_variant_is_not_bumped(catalog, today):
    hits = [s for s in catalog.all_slugs() if s.endswith(":free")]
    assert hits, "bundled snapshot has :free models"
    for slug in hits:
        for c in plan_for(catalog, today, f'"{slug}"').changes:
            assert c.to_slug.text.endswith(":free")


# --- cost-optimized --------------------------------------------------------------

SCORES = {
    "claude-opus-5": 70,
    "claude-opus-5-5": 73,
    "claude-sonnet-5-5": 71,
    "claude-haiku-5-5": 60,
    "gpt-5": 66,
    "openai/gpt-6-luna": 64,
    "deepseek/deepseek-v3.2": 58,
    "deepseek/deepseek-v4-flash": 60,
    "qwen/qwen3.8-max-0902": 70,
    "Qwen/Qwen3-235B-A22B-Instruct-2507": 55,
    "google/gemini-3.5-flash-lite": 80,
}


@pytest.fixture
def scored(catalog):
    apply_scores(catalog, SCORES, "test")
    return catalog


COST = {"strategy": "cost-optimized"}


def test_cheapest_model_that_keeps_the_score(scored, today):
    plan = plan_for(scored, today, '"claude-opus-5"', parse_config(COST))
    assert bumps(plan) == [("claude-opus-5", "claude-sonnet-5-5", "switch")]


def test_score_tolerance(scored, today):
    # gpt-6-luna scores 64 vs 66: 3% lower, outside the default 2% tolerance
    assert plan_for(scored, today, '"gpt-5"', parse_config(COST)).changes == []
    loose = parse_config({**COST, "cost-optimized": {"max-score-drop": 0.05}})
    assert bumps(plan_for(scored, today, '"gpt-5"', loose)) == [("gpt-5", "gpt-6-luna", "switch")]


def test_prefer_best_score(scored, today):
    config = parse_config({**COST, "cost-optimized": {"prefer": "best-score"}})
    assert bumps(plan_for(scored, today, '"claude-opus-5"', config)) == [
        ("claude-opus-5", "claude-opus-5-5", "minor")
    ]


def test_chinese_open_weights_stay_chinese_open_weights(scored, today):
    plan = plan_for(scored, today, '"Qwen/Qwen3-235B-A22B-Instruct-2507"', parse_config(COST))
    (change,) = plan.changes
    assert change.to_model.vendor in {"deepseek", "qwen", "moonshotai", "z-ai", "minimax", "xiaomi"}
    assert change.to_model.open_weights
    # never crosses into another ecosystem, even with a higher-scoring cheap model available
    assert change.to_model.vendor != "google"


def test_vendor_scope(scored, today):
    config = parse_config({**COST, "cost-optimized": {"scope": "vendor"}})
    plan = plan_for(scored, today, '"Qwen/Qwen3-235B-A22B-Instruct-2507"', config)
    assert all(c.to_model.vendor == "qwen" for c in plan.changes)


def test_open_weights_preserved(scored, today):
    # closed qwen3.8-max is cheaper than nothing here, but open -> closed is never allowed
    config = parse_config({**COST, "cost-optimized": {"min-savings": 0, "max-score-drop": 0.5}})
    plan = plan_for(scored, today, '"deepseek/deepseek-v3.2"', config)
    assert all(c.to_model.open_weights for c in plan.changes)


def test_missing_score_is_reported(catalog, today):
    plan = plan_for(catalog, today, '"claude-opus-5"', parse_config(COST))
    assert plan.changes == []
    assert "no benchmark score" in plan.findings[0].message


def test_retired_model_falls_back_to_same_family(catalog, today):
    plan = plan_for(catalog, today, '"claude-3-5-sonnet-20241022"', parse_config(COST))
    (change,) = plan.changes
    assert change.to_slug.text == "claude-sonnet-5-5"
    assert change.strategy == "same-family"
    assert any("cost-optimized found nothing" in n for n in change.notes)


# --- rules and ignores -----------------------------------------------------------


def test_per_path_rules(scored, today):
    config = parse_config(
        {"updates": [{"paths": ["batch/**"], "strategy": "cost-optimized"}, {"paths": ["**"]}]}
    )
    a = plan_for(scored, today, '"claude-opus-5"', config, path="batch/job.py")
    b = plan_for(scored, today, '"claude-opus-5"', config, path="api/app.py")
    assert bumps(a) == [("claude-opus-5", "claude-sonnet-5-5", "switch")]
    assert bumps(b) == [("claude-opus-5", "claude-opus-5-5", "minor")]


def test_ignore_rules(catalog, today):
    config = parse_config({"ignore": [{"model": "claude-opus-*"}]})
    plan = plan_for(catalog, today, '"claude-opus-5" "claude-sonnet-5"', config)
    assert bumps(plan) == [("claude-sonnet-5", "claude-sonnet-5-5", "minor")]

    config = parse_config({"ignore": [{"model": "claude-*", "update-types": ["major"]}]})
    plan = plan_for(catalog, today, '"claude-opus-4-5"', config)
    assert bumps(plan) == [("claude-opus-4-5", "claude-opus-4-8", "minor")]

    config = parse_config({"ignore": [{"target": "*-preview*"}], "same-family": {"allow-prerelease": True}})
    assert plan_for(catalog, today, '"google/gemini-2.5-pro"', config).changes == []


def test_grouping_and_branch_names(catalog, today):
    plan = plan_for(catalog, today, '"claude-opus-5"\n"anthropic/claude-opus-5"\n"gpt-4o-2024-08-06"')
    groups = plan.groups()
    assert [g.branch("modelbump") for g in groups] == [
        "modelbump/anthropic/claude-opus-5-to-claude-opus-5.5",
        "modelbump/openai/gpt-4o-2024-08-06-to-gpt-4o-2024-11-20",
    ]
    assert len(groups[0].changes) == 2
    assert groups[0].title() == "Bump Claude Opus 5 to Claude Opus 5.5"
    (single,) = plan.groups("single")
    assert single.branch("modelbump") == "modelbump/all-models"
    assert single.title() == "Bump 2 hard-coded LLM models"
    # the branch stays the same when only one update is left
    (one,) = plan_for(catalog, today, '"claude-opus-5"').groups("single")
    assert one.branch("modelbump") == "modelbump/all-models"
    assert one.title() == "Bump Claude Opus 5 to Claude Opus 5.5"
