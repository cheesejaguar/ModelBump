"""Which mentions get rewritten: code yes; comments, prose, model tables and
recorded data no (unless configured)."""

import json

import pytest

from modelbump.config import parse_config
from modelbump.planner import build_plan
from modelbump.scanner import Matcher, scan, scan_text


def contexts(catalog, path, text):
    return [(o.text, o.context) for o in scan_text(path, text, Matcher(catalog.all_slugs()))]


def bumped(catalog, today, path, text, config=None):
    config = config or parse_config({})
    occ = scan_text(path, text, Matcher(catalog.all_slugs()))
    return [c.from_slug.text for c in build_plan(occ, catalog, config, today).changes]


@pytest.mark.parametrize(
    "path,text,expected",
    [
        ("a.py", 'MODEL = "gpt-4o"  # was gpt-4o-mini', [("gpt-4o", "code"), ("gpt-4o-mini", "comment")]),
        ("a.py", "# use claude-opus-5 here", [("claude-opus-5", "comment")]),
        ("a.ts", "// claude-opus-5 is the default", [("claude-opus-5", "comment")]),
        ("a.ts", " * @default claude-opus-5", [("claude-opus-5", "comment")]),
        ("a.ts", "const url = 'https://x.dev//claude-opus-5'", [("claude-opus-5", "prose")]),
        (".env.example", "# claude-opus-5\nMODEL=claude-opus-5", [("claude-opus-5", "comment"), ("claude-opus-5", "code")]),
        ("README.md", "We use claude-opus-5 everywhere.", [("claude-opus-5", "prose")]),
        ("README.md", "Set `claude-opus-5` as the model.", [("claude-opus-5", "code")]),
        ("README.md", "```python\nmodel = 'claude-opus-5'\n# claude-opus-5 note\n```", [("claude-opus-5", "code"), ("claude-opus-5", "comment")]),
        ("index.html", '<option value="claude-opus-5">claude-opus-5</option>', [("claude-opus-5", "code"), ("claude-opus-5", "prose")]),
        ("a.py", "<!-- claude-opus-5 -->", [("claude-opus-5", "comment")]),
        ("a.md", "[pricing](https://example.com/pricing#gpt-4o)", [("gpt-4o", "prose")]),
    ],
)
def test_context(catalog, path, text, expected):
    assert contexts(catalog, path, text) == expected


def test_notebook_cells(catalog):
    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["We compare claude-opus-5 here\n"]},
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {},
                "outputs": [{"name": "stdout", "output_type": "stream", "text": ["model=claude-opus-5\n"]}],
                "source": ["# claude-opus-5 is fast\n", 'client.messages.create(model="claude-opus-5")\n'],
            },
        ]
    }
    text = json.dumps(nb, indent=1)
    assert [c for _, c in contexts(catalog, "nb.ipynb", text)] == ["prose", "prose", "comment", "code"]


def test_regex_literals_are_not_slugs(catalog):
    assert contexts(catalog, "a.py", r're.compile(r"^gpt-5(?:-\d{4})?$")') == []
    assert contexts(catalog, "a.py", r're.compile(r"^gpt-5\.4-nano$")') == []


def test_comments_and_prose_can_be_opted_in(catalog, today):
    text = "# default: claude-opus-5\n"
    assert bumped(catalog, today, "a.py", text) == []
    assert bumped(catalog, today, "a.py", text, parse_config({"detection": {"comments": True}})) == ["claude-opus-5"]
    assert bumped(catalog, today, "a.md", "Uses claude-opus-5.", parse_config({"detection": {"prose": True}})) == [
        "claude-opus-5"
    ]


# --- model lists ---------------------------------------------------------------------


def test_files_listing_many_versions_are_left_alone(catalog, today):
    text = 'MODELS = ["claude-opus-4-5", "claude-opus-4-6", "claude-opus-4-7"]\n'
    assert bumped(catalog, today, "registry.py", text) == []
    config = parse_config({"detection": {"model-lists": True}})
    assert len(bumped(catalog, today, "registry.py", text, config)) == 3


def test_files_listing_many_models_are_left_alone(catalog, today):
    slugs = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "gpt-5", "gpt-5-mini", "o3", "grok-4.3",
             "gemini-2.5-flash"]  # fmt: skip
    text = "\n".join(f'"{s}",' for s in slugs)
    assert bumped(catalog, today, "models.ts", text) == []


def test_mapping_keys_are_left_alone_with_their_line(catalog, today):
    text = 'PRICES = {"claude-opus-5": {"name": "claude-opus-5", "in": 5}}\nDEFAULT = "claude-sonnet-5"\n'
    assert bumped(catalog, today, "prices.py", text) == ["claude-sonnet-5"]
    yaml_text = "claude-opus-5:\n  max_tokens: 1000\ndefault: claude-sonnet-5\n"
    assert bumped(catalog, today, "models.yml", yaml_text) == ["claude-sonnet-5"]


def test_python_if_is_not_a_mapping_key(catalog, today):
    assert bumped(catalog, today, "a.py", 'if model == "claude-opus-5":\n    pass\n') == ["claude-opus-5"]


def test_target_already_in_file_is_not_duplicated(catalog, today):
    text = 'FAST = "claude-opus-5"\nBEST = "claude-opus-5-5"\n'
    assert bumped(catalog, today, "a.py", text) == []


def test_comparisons_are_flagged(catalog, today):
    config = parse_config({})
    occ = scan_text("a.py", 'pdf = "claude-opus-5" in model.name\n', Matcher(catalog.all_slugs()))
    (change,) = build_plan(occ, catalog, config, today).changes
    assert any("compares against" in n for n in change.notes)


# --- files that are never scanned ----------------------------------------------------


def test_history_fixtures_and_data_are_excluded(catalog, repo):
    files = {
        "app.py": '"claude-opus-5"',
        "docs/changelog.md": "`claude-opus-5`",
        "HISTORY.rst": "`claude-opus-5`",
        "tests/cassettes/run.yaml": "model: claude-opus-5",
        "tests/fixtures/chat.txt": '"claude-opus-5"',
        "src/__fixtures__/stream.ts": '"claude-opus-5"',
        "data/evals.jsonl": '{"model": "claude-opus-5"}',
        "chart.svg": '<text>"claude-opus-5"</text>',
        "site/_posts/2025-01-01-launch.md": "`claude-opus-5`",
        "big.json": '{"model": "claude-opus-5"}' + " " * 600_000,
    }
    for rel, content in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content)
    assert [o.path for o in scan(repo, catalog, parse_config({}))] == ["app.py"]


# --- same-family guards --------------------------------------------------------------


def test_instruct_models_are_their_own_line(catalog, today):
    assert bumped(catalog, today, "a.py", '"gpt-3.5-turbo-instruct"') == []


def test_price_increase_guard(catalog, today):
    config = parse_config({})
    occ = scan_text("a.py", '"gpt-3.5-turbo"', Matcher(catalog.all_slugs()))
    plan = build_plan(occ, catalog, config, today)
    assert plan.changes == []
    assert "costs 20.0x as much" in plan.findings[0].message
    loose = parse_config({"same-family": {"max-price-increase": None}})
    assert [c.to_slug.text for c in build_plan(occ, catalog, loose, today).changes] == ["gpt-4-turbo"]


def test_price_guard_falls_back_to_an_affordable_release_with_a_note(catalog, today):
    config = parse_config({"same-family": {"max-price-increase": 2}})
    occ = scan_text("a.py", '"gpt-5"', Matcher(catalog.all_slugs()))
    (change,) = build_plan(occ, catalog, config, today).changes
    assert change.to_slug.text == "gpt-5.4"
    assert any("Skipped the newer GPT-5.5" in n for n in change.notes)


def test_vertex_ids_only_move_to_vertex_ids(catalog, today):
    assert [c for c in bumped(catalog, today, "a.py", '"claude-haiku-4-5@20251001"')] == []
    plan_cfg = parse_config({})
    occ = scan_text("a.py", '"claude-opus-4-5@20251101"', Matcher(catalog.all_slugs()))
    (change,) = build_plan(occ, catalog, plan_cfg, today).changes
    assert change.to_slug.text == "claude-opus-5-5" and change.to_slug.style == "vertex"
