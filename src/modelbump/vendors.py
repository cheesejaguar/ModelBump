"""Registry of model vendors ModelBump understands.

Only vendors listed here are cataloged, detected, and bumped. Everything else
is ignored, so a slug from an unknown lab is never rewritten on a guess.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Vendor:
    key: str
    name: str
    # Prefixes used by OpenRouter (and the Vercel AI Gateway), e.g. "anthropic/".
    openrouter_prefixes: tuple[str, ...]
    # Hugging Face organisations that publish this vendor's open weights.
    hf_orgs: tuple[str, ...] = ()
    # Default ecosystem: cost-optimized swaps stay inside one ecosystem.
    ecosystem: str = ""
    # Versions written with hyphens, e.g. claude-opus-4-5 == 4.5.
    hyphen_versions: bool = False
    # The first-party API id equals the OpenRouter id minus its prefix
    # (openai/gpt-5.4 -> gpt-5.4). Such ids are marked "derived" until a live
    # first-party model list confirms them.
    derive_native: bool = False
    # Where to read about breaking changes when moving between models.
    migration_url: str | None = None


VENDORS: dict[str, Vendor] = {
    v.key: v
    for v in [
        Vendor(
            "anthropic",
            "Anthropic",
            ("anthropic",),
            ecosystem="anthropic",
            hyphen_versions=True,
            migration_url="https://platform.claude.com/docs/en/about-claude/models/migration-guide",
        ),
        Vendor(
            "openai",
            "OpenAI",
            ("openai",),
            hf_orgs=("openai",),
            ecosystem="openai",
            derive_native=True,
            migration_url="https://platform.openai.com/docs/models",
        ),
        Vendor(
            "google",
            "Google",
            ("google",),
            hf_orgs=("google",),
            ecosystem="google",
            derive_native=True,
            migration_url="https://ai.google.dev/gemini-api/docs/models",
        ),
        Vendor("x-ai", "xAI", ("x-ai",), ecosystem="xai", derive_native=True),
        Vendor("meta-llama", "Meta", ("meta-llama",), hf_orgs=("meta-llama",), ecosystem="meta"),
        Vendor(
            "mistralai",
            "Mistral",
            ("mistralai",),
            hf_orgs=("mistralai",),
            ecosystem="mistral",
            hyphen_versions=True,
        ),
        # Chinese labs share one ecosystem so open-weight models can be swapped
        # for each other in cost-optimized mode.
        Vendor("deepseek", "DeepSeek", ("deepseek",), hf_orgs=("deepseek-ai",), ecosystem="chinese-open"),
        Vendor("qwen", "Qwen (Alibaba)", ("qwen",), hf_orgs=("Qwen",), ecosystem="chinese-open"),
        Vendor("moonshotai", "Moonshot AI", ("moonshotai",), hf_orgs=("moonshotai",), ecosystem="chinese-open"),
        Vendor("z-ai", "Z.ai (Zhipu)", ("z-ai",), hf_orgs=("zai-org", "THUDM"), ecosystem="chinese-open"),
        Vendor("minimax", "MiniMax", ("minimax",), hf_orgs=("MiniMaxAI",), ecosystem="chinese-open"),
        Vendor("xiaomi", "Xiaomi", ("xiaomi",), hf_orgs=("XiaomiMiMo",), ecosystem="chinese-open"),
        Vendor("stepfun", "StepFun", ("stepfun", "stepfun-ai"), hf_orgs=("stepfun-ai",), ecosystem="chinese-open"),
        Vendor("tencent", "Tencent", ("tencent",), hf_orgs=("tencent",), ecosystem="chinese-open"),
        Vendor("baidu", "Baidu", ("baidu",), hf_orgs=("baidu",), ecosystem="chinese-open"),
        Vendor(
            "bytedance-seed",
            "ByteDance Seed",
            ("bytedance-seed", "bytedance"),
            hf_orgs=("ByteDance-Seed",),
            ecosystem="bytedance",
        ),
    ]
}

_BY_OPENROUTER_PREFIX = {p: v for v in VENDORS.values() for p in v.openrouter_prefixes}
_BY_HF_ORG = {o.lower(): v for v in VENDORS.values() for o in v.hf_orgs}

# Artificial Analysis names some creators differently from OpenRouter.
_AA_CREATOR_ALIASES = {
    "alibaba": "qwen",
    "zai": "z-ai",
    "zhipu": "z-ai",
    "zhipu-ai": "z-ai",
    "xai": "x-ai",
    "meta": "meta-llama",
    "mistral": "mistralai",
    "kimi": "moonshotai",
    "moonshot": "moonshotai",
    "bytedance": "bytedance-seed",
    "minimax-ai": "minimax",
}


def vendor_for_openrouter_prefix(prefix: str) -> Vendor | None:
    return _BY_OPENROUTER_PREFIX.get(prefix)


def vendor_for_hf_org(org: str) -> Vendor | None:
    return _BY_HF_ORG.get(org.lower())


def vendor_for_aa_creator(slug: str) -> Vendor | None:
    slug = slug.lower()
    return VENDORS.get(_AA_CREATOR_ALIASES.get(slug, slug))


def default_ecosystems() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for v in VENDORS.values():
        groups.setdefault(v.ecosystem, []).append(v.key)
    return groups
