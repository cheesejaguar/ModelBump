import pytest

from modelbump.slugs import parse, parse_styled, update_type


@pytest.mark.parametrize(
    "vendor,name,family,version,snapshot,pre",
    [
        # Anthropic: tier-first, hyphenated versions, dated snapshots
        ("anthropic", "claude-opus-4-5", "claude-opus", (4, 5), None, False),
        ("anthropic", "claude-opus-4-5-20251101", "claude-opus", (4, 5), "20251101", False),
        ("anthropic", "claude-opus-4-20250514", "claude-opus", (4,), "20250514", False),
        ("anthropic", "claude-opus-4-0", "claude-opus", (4,), None, False),
        ("anthropic", "claude-opus-5", "claude-opus", (5,), None, False),
        ("anthropic", "claude-opus-5-5", "claude-opus", (5, 5), None, False),
        ("anthropic", "claude-opus-5.5", "claude-opus", (5, 5), None, False),
        ("anthropic", "claude-fable-5-1", "claude-fable", (5, 1), None, False),
        ("anthropic", "claude-haiku-4.5", "claude-haiku", (4, 5), None, False),
        # Anthropic legacy: version before tier
        ("anthropic", "claude-3-5-sonnet-20241022", "claude-sonnet", (3, 5), "20241022", False),
        ("anthropic", "claude-3.7-sonnet", "claude-sonnet", (3, 7), None, False),
        ("anthropic", "claude-3-opus-20240229", "claude-opus", (3,), "20240229", False),
        ("anthropic", "claude-3-5-sonnet-v2", "claude-sonnet", (3, 5), "v2", False),
        # OpenAI
        ("openai", "gpt-5", "gpt", (5,), None, False),
        ("openai", "gpt-5.4-mini", "gpt-mini", (5, 4), None, False),
        ("openai", "gpt-4o", "gpt-o", (4,), None, False),
        ("openai", "gpt-4o-2024-08-06", "gpt-o", (4,), "20240806", False),
        ("openai", "gpt-4o-mini-2024-07-18", "gpt-o-mini", (4,), "20240718", False),
        ("openai", "gpt-4.1-nano", "gpt-nano", (4, 1), None, False),
        ("openai", "gpt-3.5-turbo-0125", "gpt-turbo", (3, 5), "0125", False),
        ("openai", "gpt-5.6-luna-pro", "gpt-luna-pro", (5, 6), None, False),
        ("openai", "gpt-6-luna-pro", "gpt-luna-pro", (6,), None, False),
        ("openai", "o3", "o", (3,), None, False),
        ("openai", "o4-mini-2025-04-16", "o-mini", (4,), "20250416", False),
        # Google
        ("google", "gemini-2.5-flash-lite", "gemini-flash-lite", (2, 5), None, False),
        ("google", "gemini-3.1-pro-preview", "gemini-pro", (3, 1), None, True),
        ("google", "gemini-2.5-pro-preview-06-05", "gemini-pro", (2, 5), "0605", True),
        ("google", "gemini-2.0-flash-001", "gemini-flash", (2,), "001", False),
        ("google", "gemma-3-27b-it", "gemma-27b", (3,), None, False),
        # xAI
        ("x-ai", "grok-4.20-multi-agent", "grok-multi-agent", (4, 20), None, False),
        # Chinese open-weight labs
        ("deepseek", "deepseek-v3.2", "deepseek", (3, 2), None, False),
        ("deepseek", "deepseek-chat-v3.1", "deepseek", (3, 1), None, False),
        ("deepseek", "deepseek-v4-flash-0731", "deepseek-flash", (4,), "0731", False),
        ("deepseek", "deepseek-v4.1-flash", "deepseek-flash", (4, 1), None, False),
        ("deepseek", "deepseek-r1-0528", "deepseek-r", (1,), "0528", False),
        ("qwen", "qwen3.8-max-0902", "qwen-max", (3, 8), "0902", False),
        ("qwen", "qwen3.5-plus-02-15", "qwen-plus", (3, 5), "0215", False),
        ("qwen", "qwen3-235b-a22b-thinking-2507", "qwen-235b-a22b-thinking", (3,), "2507", False),
        ("qwen", "Qwen3-235B-A22B-Instruct-2507", "qwen-235b-a22b", (3,), "2507", False),
        ("qwen", "qwen-2.5-72b-instruct", "qwen-72b", (2, 5), None, False),
        ("moonshotai", "kimi-k2.6", "kimi-k", (2, 6), None, False),
        ("moonshotai", "kimi-k3", "kimi-k", (3,), None, False),
        ("moonshotai", "kimi-k2-0905", "kimi-k", (2,), "0905", False),
        ("z-ai", "glm-4.5-air", "glm-air", (4, 5), None, False),
        ("z-ai", "glm-4.5v", "glm-v", (4, 5), None, False),
        ("minimax", "minimax-m2.7", "minimax-m", (2, 7), None, False),
        ("xiaomi", "mimo-v2.6-pro", "mimo-pro", (2, 6), None, False),
        ("tencent", "hy3", "hy", (3,), None, False),
        ("meta-llama", "llama-3.1-8b-instruct", "llama-8b", (3, 1), None, False),
        ("mistralai", "mistral-medium-3-5", "mistral-medium", (3, 5), None, False),
    ],
)
def test_parse(vendor, name, family, version, snapshot, pre):
    p = parse(vendor, name)
    assert p is not None, name
    assert p.identity.family == family
    assert p.identity.version == version
    assert p.snapshot == snapshot
    assert p.identity.prerelease is pre


@pytest.mark.parametrize(
    "vendor,name",
    [
        ("openai", "gpt-oss-120b"),  # size only, no version
        ("openai", "gpt-chat-latest"),
        ("deepseek", "deepseek-chat"),
        ("mistralai", "mistral-large-2512"),
        ("qwen", "qwen-plus"),
        ("unknown-lab", "model-1"),
    ],
)
def test_unversioned_names_are_not_parsed(vendor, name):
    assert parse(vendor, name) is None


def test_same_model_in_every_style_has_one_identity():
    native = parse_styled("anthropic", "native", "claude-opus-4-5-20251101")
    router = parse_styled("anthropic", "openrouter", "anthropic/claude-opus-4.5")
    bedrock = parse_styled("anthropic", "bedrock", "anthropic.claude-opus-4-5-20251101-v1:0")
    vertex = parse_styled("anthropic", "vertex", "claude-opus-4-5@20251101")
    assert native.identity == router.identity == bedrock.identity == vertex.identity
    assert bedrock.snapshot == "20251101-v1:0"
    assert vertex.snapshot == "20251101"


def test_update_types():
    opus5 = parse("anthropic", "claude-opus-5").identity
    opus55 = parse("anthropic", "claude-opus-5-5").identity
    opus48 = parse("anthropic", "claude-opus-4-8").identity
    sonnet = parse("anthropic", "claude-sonnet-5-5").identity
    assert update_type(opus5, opus55) == "minor"
    assert update_type(opus48, opus55) == "major"
    assert update_type(opus5, sonnet) == "switch"
    assert update_type(opus5, opus5) == "snapshot"
