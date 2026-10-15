import datetime as dt

from modelbump.catalog import Catalog, apply_scores, load_aa_scores, load_entries, load_native_list


def test_one_model_collects_every_host_slug(catalog):
    m = catalog.models["anthropic/claude-opus@4.5"]
    by_style = {(s.style, s.text) for s in m.slugs}
    assert ("native", "claude-opus-4-5") in by_style
    assert ("native", "claude-opus-4-5-20251101") in by_style
    assert ("bedrock", "anthropic.claude-opus-4-5-20251101-v1:0") in by_style
    assert ("vertex", "claude-opus-4-5@20251101") in by_style
    assert ("openrouter", "anthropic/claude-opus-4.5") in by_style
    assert ("openrouter", "anthropic/claude-opus-4.5:batch") in by_style


def test_bedrock_mantle_ids_are_their_own_style(catalog):
    m = catalog.models["anthropic/claude-haiku@4.5"]
    styles = {s.text: s.style for s in m.slugs}
    assert styles["anthropic.claude-haiku-4-5"] == "bedrock-mantle"
    assert styles["anthropic.claude-haiku-4-5-20251001-v1:0"] == "bedrock"


def test_prices_come_from_the_floating_openrouter_id(catalog):
    gpt4o = catalog.models["openai/gpt-o@4"]
    # openai/gpt-4o-2024-05-13 is $5/$15; the floating gpt-4o is $2.50/$10
    assert (gpt4o.price_in, gpt4o.price_out) == (2.5, 10.0)
    assert gpt4o.display_name == "GPT-4o"


def test_openrouter_data_overrides_seed_prices_but_not_lifecycle(catalog, today):
    opus41 = catalog.models["anthropic/claude-opus@4.1"]
    assert opus41.lifecycle(today) == "retired"  # seed: retires 2026-08-05
    assert opus41.price_in == 15.0


def test_open_weights_and_hf_ids(catalog):
    ds = catalog.models["deepseek/deepseek@3.2"]
    assert ds.open_weights
    assert any(s.style == "hf" and s.text == "deepseek-ai/DeepSeek-V3.2" for s in ds.slugs)
    assert not catalog.models["anthropic/claude-opus@5.5"].open_weights


def test_derived_native_ids_are_marked(catalog):
    hit = catalog.lookup("gpt-5.5")
    assert hit and hit[1].derived
    seeded = catalog.lookup("gpt-4o-2024-08-06")
    assert seeded and not seeded[1].derived


def test_openrouter_effort_aliases_are_not_native(catalog):
    assert catalog.lookup("openai/o4-mini-high") is not None
    assert catalog.lookup("o4-mini-high") is None


def test_native_list_confirms_and_prunes_derived_ids(catalog):
    load_native_list(
        catalog,
        "openai",
        [("gpt-5.5", dt.date(2026, 4, 24)), ("text-embedding-3-large", None)],
        source="openai",
        create_models=False,
    )
    assert not catalog.lookup("gpt-5.5")[1].derived
    assert catalog.lookup("gpt-5.4") is None  # derived, not in the live list
    assert catalog.lookup("openai/gpt-5.4") is not None  # OpenRouter id untouched
    assert catalog.lookup("text-embedding-3-large") is None  # never creates non-chat models
    assert catalog.lookup("gpt-4o-2024-08-06") is not None  # seed ids are not derived


def test_native_list_can_add_new_claude_models(catalog):
    load_native_list(catalog, "anthropic", [("claude-opus-6", None)], source="anthropic", create_models=True)
    assert catalog.lookup("claude-opus-6")[0].key == "anthropic/claude-opus@6"


def test_artificial_analysis_scores_match_by_identity(catalog):
    records = [
        {"slug": "claude-opus-5-5", "model_creator": {"slug": "anthropic"},
         "evaluations": {"artificial_analysis_intelligence_index": 73.0}},
        {"slug": "claude-4-5-sonnet-thinking", "model_creator": {"slug": "anthropic"},
         "evaluations": {"artificial_analysis_intelligence_index": 61.0}},
        {"slug": "claude-4-5-sonnet", "model_creator": {"slug": "anthropic"},
         "evaluations": {"artificial_analysis_intelligence_index": 55.0}},
        {"slug": "gpt-5-1-high", "model_creator": {"slug": "openai"},
         "evaluations": {"artificial_analysis_intelligence_index": 70.0}},
        {"slug": "deepseek-v3-2-reasoning", "model_creator": {"slug": "deepseek"},
         "evaluations": {"artificial_analysis_intelligence_index": 57.0}},
        {"slug": "qwen3-8-max", "model_creator": {"slug": "alibaba"},
         "evaluations": {"artificial_analysis_intelligence_index": 69.0}},
        {"slug": "some-unknown-model", "model_creator": {"slug": "nobody"},
         "evaluations": {"artificial_analysis_intelligence_index": 99.0}},
    ]  # fmt: skip
    assert load_aa_scores(catalog, records, "intelligence") == 5
    assert catalog.models["anthropic/claude-opus@5.5"].score == 73.0
    assert catalog.models["anthropic/claude-sonnet@4.5"].score == 61.0  # best effort variant wins
    assert catalog.models["openai/gpt@5.1"].score == 70.0
    assert catalog.models["deepseek/deepseek@3.2"].score == 57.0
    assert catalog.models["qwen/qwen-max@3.8"].score == 69.0


def test_config_scores_override_artificial_analysis(catalog):
    load_aa_scores(
        catalog,
        [{"slug": "claude-opus-5", "model_creator": {"slug": "anthropic"},
          "evaluations": {"artificial_analysis_intelligence_index": 70.0}}],
        "intelligence",
    )  # fmt: skip
    apply_scores(catalog, {"anthropic/claude-opus-5": 72.5, "nope-1": 1}, "config")
    m = catalog.models["anthropic/claude-opus@5"]
    assert (m.score, m.score_source) == (72.5, "config")
    assert any("nope-1" in n for n in catalog.notes)


def test_config_models_extend_the_catalog():
    cat = Catalog()
    load_entries(
        cat,
        [{"vendor": "anthropic", "name": "Claude Opus 6", "native": ["claude-opus-6"], "score": 80,
          "pricing": {"input": 4, "output": 20}}],
        "config",
    )  # fmt: skip
    m = cat.lookup("claude-opus-6")[0]
    assert (m.name, m.score, m.price_in) == ("Claude Opus 6", 80.0, 4)
