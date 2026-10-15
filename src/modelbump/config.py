"""Load and validate ``.github/modelbump.yml``."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .vendors import VENDORS, default_ecosystems

CONFIG_LOCATIONS = (
    ".github/modelbump.yml",
    ".github/modelbump.yaml",
    ".modelbump.yml",
    ".modelbump.yaml",
)
STRATEGIES = ("same-family", "cost-optimized")
UPDATE_TYPES = ("snapshot", "minor", "major")
CATALOG_SOURCES = ("openrouter", "anthropic", "openai", "artificial-analysis")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SameFamily:
    update_types: tuple[str, ...] = UPDATE_TYPES
    allow_prerelease: bool = False


@dataclass(frozen=True)
class CostOptimized:
    scope: str = "ecosystem"  # "vendor" or "ecosystem"
    max_score_drop: float = 0.02  # candidate score >= current * (1 - drop)
    min_savings: float = 0.10  # candidate blended price <= current * (1 - savings)
    price_weights: tuple[float, float] = (3.0, 1.0)  # input:output token mix
    prefer: str = "cheapest"  # or "best-score"
    preserve_open_weights: bool = True
    allow_older: bool = False
    allow_prerelease: bool = False
    require_context: bool = True


@dataclass(frozen=True)
class Rule:
    paths: tuple[str, ...] = ("**",)
    exclude: tuple[str, ...] = ()
    strategy: str = "same-family"
    same_family: SameFamily = SameFamily()
    cost_optimized: CostOptimized = CostOptimized()

    def matches(self, path: str) -> bool:
        return any(glob_match(path, p) for p in self.paths) and not any(glob_match(path, p) for p in self.exclude)


@dataclass(frozen=True)
class IgnoreRule:
    model: str | None = None
    target: str | None = None
    paths: tuple[str, ...] = ()
    update_types: tuple[str, ...] = ()

    def blocks(self, path: str, from_slug: str, to_slug: str | None, kind: str | None) -> bool:
        if self.model and not fnmatch.fnmatchcase(from_slug, self.model):
            return False
        if self.paths and not any(glob_match(path, p) for p in self.paths):
            return False
        if self.target and (to_slug is None or not fnmatch.fnmatchcase(to_slug, self.target)):
            return False
        if self.update_types and (kind is None or kind not in self.update_types):
            return False
        return True


@dataclass(frozen=True)
class CatalogConfig:
    sources: tuple[str, ...] = CATALOG_SOURCES
    cache_ttl_hours: float = 24.0
    benchmark: str = "intelligence"
    scores: dict[str, float] = field(default_factory=dict)
    scores_file: str | None = None
    models: tuple[dict[str, Any], ...] = ()
    derive_native_slugs: bool = True


@dataclass(frozen=True)
class PullRequests:
    grouping: str = "per-model"  # or "single"
    branch_prefix: str = "modelbump"
    labels: tuple[str, ...] = ("dependencies", "llm-models")
    reviewers: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    commit_message_prefix: str = "chore(models):"
    open_pull_requests_limit: int = 5
    base: str | None = None


@dataclass(frozen=True)
class Config:
    rules: tuple[Rule, ...] = (Rule(),)
    exclude: tuple[str, ...] = ()
    ignore: tuple[IgnoreRule, ...] = ()
    ecosystems: dict[str, list[str]] = field(default_factory=default_ecosystems)
    catalog: CatalogConfig = CatalogConfig()
    pull_requests: PullRequests = PullRequests()
    path: Path | None = None

    def rule_for(self, path: str) -> Rule | None:
        if any(glob_match(path, p) for p in self.exclude):
            return None
        return next((r for r in self.rules if r.matches(path)), None)

    def ecosystem_of(self, vendor: str) -> str:
        for name, members in self.ecosystems.items():
            if vendor in members:
                return name
        return vendor

    def with_strategy(self, strategy: str) -> Config:
        if strategy not in STRATEGIES:
            raise ConfigError(f"unknown strategy {strategy!r} (expected one of {', '.join(STRATEGIES)})")
        return replace(self, rules=tuple(replace(r, strategy=strategy) for r in self.rules))


# --- globbing -------------------------------------------------------------------

_GLOB_CACHE: dict[str, re.Pattern[str]] = {}


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(path: str, pattern: str) -> bool:
    """Gitignore-flavoured glob: ``**`` crosses directories, and a pattern
    without a slash matches the file name at any depth."""
    pattern = pattern.lstrip("/")
    rx = _GLOB_CACHE.get(pattern)
    if rx is None:
        rx = _GLOB_CACHE[pattern] = _glob_to_regex(pattern)
    if rx.match(path):
        return True
    if "/" not in pattern.rstrip("/"):
        return bool(rx.match(path.rsplit("/", 1)[-1]))
    # "dir/" or "dir/**" style prefixes
    return pattern.endswith("/") and path.startswith(pattern)


# --- parsing --------------------------------------------------------------------


def _norm(d: Any, ctx: str) -> dict[str, Any]:
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ConfigError(f"{ctx}: expected a mapping, got {type(d).__name__}")
    return {str(k).replace("_", "-"): v for k, v in d.items()}


def _check_keys(d: dict[str, Any], allowed: set[str], ctx: str) -> None:
    unknown = sorted(set(d) - allowed)
    if unknown:
        raise ConfigError(f"{ctx}: unknown key(s) {', '.join(unknown)} (allowed: {', '.join(sorted(allowed))})")


def _str_list(v: Any, ctx: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,)
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError(f"{ctx}: expected a list of strings")
    return tuple(v)


def _number(v: Any, ctx: str, lo: float | None = None, hi: float | None = None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{ctx}: expected a number")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ConfigError(f"{ctx}: must be between {lo} and {hi}")
    return float(v)


def _bool(v: Any, ctx: str) -> bool:
    if not isinstance(v, bool):
        raise ConfigError(f"{ctx}: expected true or false")
    return v


def _choice(v: Any, choices: tuple[str, ...], ctx: str) -> str:
    if v not in choices:
        raise ConfigError(f"{ctx}: expected one of {', '.join(choices)}, got {v!r}")
    return v


def _same_family(raw: Any, base: SameFamily, ctx: str) -> SameFamily:
    d = _norm(raw, ctx)
    _check_keys(d, {"update-types", "allow-prerelease"}, ctx)
    out = base
    if "update-types" in d:
        types = _str_list(d["update-types"], f"{ctx}.update-types")
        for t in types:
            _choice(t, UPDATE_TYPES, f"{ctx}.update-types")
        out = replace(out, update_types=types)
    if "allow-prerelease" in d:
        out = replace(out, allow_prerelease=_bool(d["allow-prerelease"], f"{ctx}.allow-prerelease"))
    return out


def _cost(raw: Any, base: CostOptimized, ctx: str) -> CostOptimized:
    d = _norm(raw, ctx)
    allowed = {
        "scope", "max-score-drop", "min-savings", "price-weights", "prefer",
        "preserve-open-weights", "allow-older", "allow-prerelease", "require-context",
    }  # fmt: skip
    _check_keys(d, allowed, ctx)
    out = base
    if "scope" in d:
        out = replace(out, scope=_choice(d["scope"], ("vendor", "ecosystem"), f"{ctx}.scope"))
    if "max-score-drop" in d:
        out = replace(out, max_score_drop=_number(d["max-score-drop"], f"{ctx}.max-score-drop", 0, 1))
    if "min-savings" in d:
        out = replace(out, min_savings=_number(d["min-savings"], f"{ctx}.min-savings", 0, 1))
    if "price-weights" in d:
        w = _norm(d["price-weights"], f"{ctx}.price-weights")
        _check_keys(w, {"input", "output"}, f"{ctx}.price-weights")
        out = replace(
            out,
            price_weights=(
                _number(w.get("input", out.price_weights[0]), f"{ctx}.price-weights.input", 0),
                _number(w.get("output", out.price_weights[1]), f"{ctx}.price-weights.output", 0),
            ),
        )
    if "prefer" in d:
        out = replace(out, prefer=_choice(d["prefer"], ("cheapest", "best-score"), f"{ctx}.prefer"))
    for key in ("preserve-open-weights", "allow-older", "allow-prerelease", "require-context"):
        if key in d:
            out = replace(out, **{key.replace("-", "_"): _bool(d[key], f"{ctx}.{key}")})
    return out


def parse_config(raw: Any, path: Path | None = None) -> Config:
    d = _norm(raw, "config")
    top_keys = {
        "version", "strategy", "same-family", "cost-optimized", "updates", "exclude",
        "ignore", "ecosystems", "catalog", "pull-requests",
    }  # fmt: skip
    _check_keys(d, top_keys, "config")
    if d.get("version", 1) != 1:
        raise ConfigError("config: only version 1 is supported")

    strategy = _choice(d.get("strategy", "same-family"), STRATEGIES, "strategy")
    same = _same_family(d.get("same-family"), SameFamily(), "same-family")
    cost = _cost(d.get("cost-optimized"), CostOptimized(), "cost-optimized")

    rules: list[Rule] = []
    updates = d.get("updates")
    if updates is None:
        rules.append(Rule(strategy=strategy, same_family=same, cost_optimized=cost))
    else:
        if not isinstance(updates, list) or not updates:
            raise ConfigError("updates: expected a non-empty list")
        for i, entry in enumerate(updates):
            ctx = f"updates[{i}]"
            e = _norm(entry, ctx)
            _check_keys(e, {"paths", "exclude", "strategy", "same-family", "cost-optimized"}, ctx)
            rules.append(
                Rule(
                    paths=_str_list(e.get("paths", ["**"]), f"{ctx}.paths") or ("**",),
                    exclude=_str_list(e.get("exclude"), f"{ctx}.exclude"),
                    strategy=_choice(e.get("strategy", strategy), STRATEGIES, f"{ctx}.strategy"),
                    same_family=_same_family(e.get("same-family"), same, f"{ctx}.same-family"),
                    cost_optimized=_cost(e.get("cost-optimized"), cost, f"{ctx}.cost-optimized"),
                )
            )

    ignores: list[IgnoreRule] = []
    for i, entry in enumerate(d.get("ignore") or []):
        ctx = f"ignore[{i}]"
        e = _norm(entry, ctx)
        _check_keys(e, {"model", "target", "paths", "update-types"}, ctx)
        types = _str_list(e.get("update-types"), f"{ctx}.update-types")
        for t in types:
            _choice(t, (*UPDATE_TYPES, "switch"), f"{ctx}.update-types")
        rule = IgnoreRule(
            model=e.get("model"),
            target=e.get("target"),
            paths=_str_list(e.get("paths"), f"{ctx}.paths"),
            update_types=types,
        )
        if not (rule.model or rule.target or rule.paths):
            raise ConfigError(f"{ctx}: needs at least one of model, target, paths")
        ignores.append(rule)

    ecosystems = default_ecosystems()
    if d.get("ecosystems") is not None:
        eco = _norm(d["ecosystems"], "ecosystems")
        for name, members in eco.items():
            vendors = list(_str_list(members, f"ecosystems.{name}"))
            unknown = [v for v in vendors if v not in VENDORS]
            if unknown:
                raise ConfigError(
                    f"ecosystems.{name}: unknown vendor(s) {', '.join(unknown)} (known: {', '.join(VENDORS)})"
                )
            for other in ecosystems.values():  # a vendor belongs to one ecosystem
                other[:] = [v for v in other if v not in vendors]
            ecosystems[name] = vendors
        ecosystems = {k: v for k, v in ecosystems.items() if v}

    cat = _norm(d.get("catalog"), "catalog")
    _check_keys(
        cat,
        {"sources", "cache-ttl-hours", "benchmark", "scores", "scores-file", "models", "derive-native-slugs"},
        "catalog",
    )
    sources = _str_list(cat.get("sources", list(CATALOG_SOURCES)), "catalog.sources")
    for s in sources:
        _choice(s, CATALOG_SOURCES, "catalog.sources")
    scores = cat.get("scores") or {}
    if not isinstance(scores, dict):
        raise ConfigError("catalog.scores: expected a mapping of model slug -> score")
    models = cat.get("models") or []
    if not isinstance(models, list):
        raise ConfigError("catalog.models: expected a list")
    catalog = CatalogConfig(
        sources=sources,
        cache_ttl_hours=_number(cat.get("cache-ttl-hours", 24), "catalog.cache-ttl-hours", 0),
        benchmark=str(cat.get("benchmark", "intelligence")),
        scores={str(k): _number(v, f"catalog.scores.{k}") for k, v in scores.items()},
        scores_file=cat.get("scores-file"),
        models=tuple(models),
        derive_native_slugs=_bool(cat.get("derive-native-slugs", True), "catalog.derive-native-slugs"),
    )

    pr = _norm(d.get("pull-requests"), "pull-requests")
    _check_keys(
        pr,
        {
            "grouping", "branch-prefix", "labels", "reviewers", "assignees",
            "commit-message-prefix", "open-pull-requests-limit", "base",
        },
        "pull-requests",
    )  # fmt: skip
    pulls = PullRequests(
        grouping=_choice(pr.get("grouping", "per-model"), ("per-model", "single"), "pull-requests.grouping"),
        branch_prefix=str(pr.get("branch-prefix", "modelbump")).strip("/"),
        labels=_str_list(pr.get("labels", ["dependencies", "llm-models"]), "pull-requests.labels"),
        reviewers=_str_list(pr.get("reviewers"), "pull-requests.reviewers"),
        assignees=_str_list(pr.get("assignees"), "pull-requests.assignees"),
        commit_message_prefix=str(pr.get("commit-message-prefix", "chore(models):")),
        open_pull_requests_limit=int(
            _number(pr.get("open-pull-requests-limit", 5), "pull-requests.open-pull-requests-limit", 0)
        ),
        base=pr.get("base"),
    )

    return Config(
        rules=tuple(rules),
        exclude=_str_list(d.get("exclude"), "exclude"),
        ignore=tuple(ignores),
        ecosystems=ecosystems,
        catalog=catalog,
        pull_requests=pulls,
        path=path,
    )


def find_config(root: Path) -> Path | None:
    for loc in CONFIG_LOCATIONS:
        p = root / loc
        if p.is_file():
            return p
    return None


def load_config(root: Path, explicit: Path | None = None) -> Config:
    path = explicit or find_config(root)
    if path is None:
        return Config()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    try:
        return parse_config(raw, path)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e
