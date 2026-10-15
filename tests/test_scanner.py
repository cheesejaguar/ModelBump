from modelbump.config import Config, parse_config
from modelbump.scanner import Matcher, scan, scan_text


def found(catalog, text):
    return [o.text for o in scan_text("f.py", text, Matcher(catalog.all_slugs()))]


def test_slug_does_not_match_inside_a_longer_slug(catalog):
    assert found(catalog, 'a = "claude-opus-5-5"') == ["claude-opus-5-5"]
    assert found(catalog, 'a = "claude-opus-5"') == ["claude-opus-5"]
    assert found(catalog, 'a = "gpt-5.9"') == []  # unknown version, gpt-5 must not match
    assert found(catalog, 'a = "claude-opus-5-turbo"') == []
    assert found(catalog, "load('gpt-5.json')") == []


def test_longest_known_slug_wins(catalog):
    assert found(catalog, '"claude-opus-4-5-20251101"') == ["claude-opus-4-5-20251101"]
    assert found(catalog, '"gpt-4o-mini"') == ["gpt-4o-mini"]


def test_host_prefixes_and_suffixes(catalog):
    assert found(catalog, '"us.anthropic.claude-sonnet-4-20250514-v1:0"') == [
        "anthropic.claude-sonnet-4-20250514-v1:0"
    ]
    assert found(catalog, '"anthropic/claude-opus-4.5:batch"') == ["anthropic/claude-opus-4.5:batch"]
    assert found(catalog, '"openai/gpt-5.4:nitro"') == ["openai/gpt-5.4"]  # routing suffix kept outside
    assert found(catalog, '"openrouter/anthropic/claude-sonnet-4.5"') == ["anthropic/claude-sonnet-4.5"]
    assert found(catalog, '"claude-opus-4-5@20251101"') == ["claude-opus-4-5@20251101"]


def test_unknown_bedrock_and_vertex_ids_are_left_alone(catalog):
    # The bare first-party id inside an unknown host id must not be rewritten.
    assert found(catalog, '"anthropic.claude-opus-4-6"') == []
    assert found(catalog, '"claude-opus-4-6@20260205"') == []


def test_bare_slugs_only_match_when_quoted(catalog):
    assert found(catalog, 'model = "o3"') == ["o3"]
    assert found(catalog, "o3 = compute()") == []


def test_inline_ignore_comments(catalog):
    text = 'a = "gpt-4o"  # modelbump: ignore\n# modelbump: ignore-next-line\nb = "gpt-4o"\nc = "gpt-4o"\n'
    occ = scan_text("f.py", text, Matcher(catalog.all_slugs()))
    assert [o.line for o in occ] == [4]


def test_positions_are_reported(catalog):
    occ = scan_text("f.py", 'x = 1\nmodel = "claude-opus-5"\n', Matcher(catalog.all_slugs()))[0]
    assert (occ.line, occ.column, occ.line_text) == (2, 10, 'model = "claude-opus-5"')


def test_scan_respects_config_excludes_and_skips_config_file(catalog, repo):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text('M = "claude-opus-5"\n')
    (repo / "docs").mkdir()
    (repo / "docs" / "notes.md").write_text("claude-opus-5\n")
    (repo / "uv.lock").write_text('"claude-opus-5"\n')
    (repo / "image.png").write_bytes(b"\x89PNG\0claude-opus-5")
    (repo / ".github").mkdir()
    cfg_path = repo / ".github" / "modelbump.yml"
    cfg_path.write_text("version: 1\nexclude: ['docs/**']\nignore:\n  - model: claude-opus-5\n")
    config = parse_config({"exclude": ["docs/**"]}, cfg_path)
    assert [o.path for o in scan(repo, catalog, config)] == ["src/a.py"]


def test_scan_without_git_walks_the_tree(catalog, tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text('"claude-opus-5"')
    (tmp_path / "a.ts").write_text('const m = "claude-opus-5";')
    assert [o.path for o in scan(tmp_path, catalog, Config())] == ["a.ts"]


def test_config_and_scores_files_are_never_scanned(catalog, repo):
    (repo / ".modelbump.yml").write_text("ignore:\n  - model: claude-opus-5\n")
    (repo / "scores.yml").write_text("claude-opus-5: 70\n")
    (repo / "a.py").write_text('"claude-opus-5"\n')
    config = parse_config({"catalog": {"scores-file": "./scores.yml"}})
    assert [o.path for o in scan(repo, catalog, config)] == ["a.py"]
