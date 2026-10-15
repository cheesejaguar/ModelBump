"""Turn scan results into a list of concrete slug replacements."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, replace

from .catalog import STYLE_LABELS, Catalog, Model, Slug
from .config import Config, Rule
from .scanner import Occurrence
from .strategies import Candidate, NoCandidates, cost_optimized, same_family
from .vendors import VENDORS

# Where a slug may be rewritten in another style when the target has no slug
# in the original one. Vertex AI serves current Claude models under the bare
# first-party id instead of an @-dated one.
STYLE_FALLBACKS = {"vertex": ("native",)}


@dataclass(frozen=True)
class Change:
    occurrence: Occurrence
    from_model: Model
    from_slug: Slug
    to_model: Model
    to_slug: Slug
    kind: str  # snapshot | minor | major | switch
    strategy: str
    notes: tuple[str, ...] = ()
    price_weights: tuple[float, float] = (3.0, 1.0)


@dataclass(frozen=True)
class Finding:
    occurrence: Occurrence
    model: Model
    level: str  # "warning" or "info"
    message: str


@dataclass
class Group:
    """Changes that land together in one pull request."""

    from_model: Model
    to_model: Model
    kind: str
    strategy: str
    changes: list[Change] = field(default_factory=list)

    @property
    def from_slugs(self) -> list[str]:
        return list(dict.fromkeys(c.from_slug.text for c in self.changes))

    @property
    def to_slugs(self) -> list[str]:
        return list(dict.fromkeys(c.to_slug.text for c in self.changes))

    @property
    def files(self) -> list[str]:
        return list(dict.fromkeys(c.occurrence.path for c in self.changes))

    def _ident(self, model: Model, slugs: list[str]) -> str:
        if self.kind == "snapshot":
            return _branch_safe(slugs[0])
        return _branch_safe(f"{model.identity.family}-{model.identity.version_str}")

    def branch(self, prefix: str) -> str:
        if self.kind == "multiple":
            return f"{prefix}/all-models"
        return f"{self.branch_stem(prefix)}{self._ident(self.to_model, self.to_slugs)}"

    def branch_stem(self, prefix: str) -> str:
        """Branch name up to the target: PRs sharing it supersede each other."""
        if self.kind == "multiple":
            return f"{prefix}/all-models"
        return f"{prefix}/{self.from_model.vendor}/{self._ident(self.from_model, self.from_slugs)}-to-"

    @property
    def price_weights(self) -> tuple[float, float]:
        return self.changes[0].price_weights if self.changes else (3.0, 1.0)

    def title(self) -> str:
        if self.kind == "multiple":
            parts = Plan([], self.changes, [], dt.date.min).groups()
            if len(parts) == 1:
                return parts[0].title()
            return f"Bump {len(parts)} hard-coded LLM models"
        if self.kind == "snapshot":
            return f"Bump {self.from_slugs[0]} to {self.to_slugs[0]}"
        if self.strategy == "cost-optimized":
            saving = savings(self.from_model, self.to_model, self.price_weights)
            suffix = f" (~{saving:.0%} cheaper)" if saving is not None else ""
            return f"Switch {self.from_model.display_name} to {self.to_model.display_name}{suffix}"
        return f"Bump {self.from_model.display_name} to {self.to_model.display_name}"


@dataclass
class Plan:
    occurrences: list[Occurrence]
    changes: list[Change]
    findings: list[Finding]
    today: dt.date

    def groups(self, grouping: str = "per-model") -> list[Group]:
        groups: dict[tuple, Group] = {}
        for c in self.changes:
            key = (c.from_model.key, c.to_model.key, c.strategy)
            if c.kind == "snapshot":
                key += (c.from_slug.text, c.to_slug.text)
            g = groups.get(key)
            if g is None:
                g = groups[key] = Group(c.from_model, c.to_model, c.kind, c.strategy)
            g.changes.append(c)
        out = list(groups.values())
        if grouping == "single" and out:
            return [_merge_groups(out)]
        return out


def _merge_groups(groups: list[Group]) -> Group:
    merged = Group(groups[0].from_model, groups[0].to_model, "multiple", "multiple")
    for g in groups:
        merged.changes.extend(g.changes)
    return merged


def _branch_safe(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-.")


def savings(old: Model, new: Model, weights: tuple[float, float]) -> float | None:
    a, b = old.blended_price(weights), new.blended_price(weights)
    if not a or b is None:
        return None
    return 1 - b / a


def render(from_slug: Slug, to_model: Model) -> tuple[Slug | None, list[str]]:
    """Pick the slug for ``to_model`` written the way ``from_slug`` was written."""
    for style in (from_slug.style, *STYLE_FALLBACKS.get(from_slug.style, ())):
        options = to_model.slugs_for(style, from_slug.modifier)
        if not options:
            continue
        notes = []
        label = STYLE_LABELS.get(style, style)
        pinned = [s for s in options if s.pinned]
        floating = [s for s in options if not s.pinned]
        if from_slug.pinned:
            choice = pinned[-1] if pinned else floating[-1]
            if not pinned and style == from_slug.style:
                notes.append(
                    f"{to_model.display_name} has no dated {label} id, so the pinned `{from_slug.text}` "
                    f"becomes the floating `{choice.text}`."
                )
        else:
            choice = floating[-1] if floating else pinned[-1]
            if not floating:
                notes.append(f"{to_model.display_name} has no floating {label} id; pinned to `{choice.text}`.")
        if style != from_slug.style:
            notes.append(
                f"{to_model.display_name} has no {STYLE_LABELS.get(from_slug.style)} snapshot id; "
                f"it is served there under the first-party id `{choice.text}`."
            )
        if choice.derived:
            vendor = VENDORS[to_model.vendor].name
            notes.append(
                f"`{choice.text}` is inferred from OpenRouter's id and has not been confirmed against "
                f"{vendor}'s model list."
            )
        return choice, notes
    return None, []


def status_text(model: Model, today: dt.date) -> str:
    life = model.lifecycle(today)
    if life == "retired" and model.retires:
        return f"retired since {model.retires.isoformat()}"
    if life == "deprecated" and model.retires:
        return f"deprecated, retires {model.retires.isoformat()}"
    return life


def lifecycle_text(model: Model, today: dt.date) -> str:
    return f"{model.display_name} is {status_text(model, today)}"


def _candidates(model: Model, rule: Rule, catalog: Catalog, config: Config, today: dt.date) -> list[Candidate]:
    if rule.strategy == "cost-optimized":
        return cost_optimized(model, catalog, rule.cost_optimized, config, today)
    return same_family(model, catalog, rule.same_family, today)


def _snapshot_bump(model: Model, slug: Slug) -> Slug | None:
    if not slug.pinned:
        return None
    newer = [s for s in model.slugs_for(slug.style, slug.modifier) if s.pinned and s.order_key() > slug.order_key()]
    return newer[-1] if newer else None


def _first_change(
    occ: Occurrence,
    model: Model,
    slug: Slug,
    candidates: list[Candidate],
    strategy: str,
    config: Config,
    unrenderable: list[str],
    price_weights: tuple[float, float],
) -> Change | None:
    for cand in candidates:
        target, notes = render(slug, cand.model)
        if target is None:
            unrenderable.append(cand.model.display_name)
            continue
        if any(r.blocks(occ.path, occ.text, target.text, cand.kind) for r in config.ignore):
            continue
        return Change(occ, model, slug, cand.model, target, cand.kind, strategy, tuple(notes), price_weights)
    return None


def _prerelease_hint(model: Model, catalog: Catalog, today: dt.date) -> str:
    previews = [
        m.display_name
        for m in catalog.family(model)
        if m.identity.prerelease
        and m.lifecycle(today) == "active"
        and m.released
        and (model.released is None or m.released > model.released)
    ]
    if not previews:
        return ""
    return f" (prerelease successor available: {previews[0]}; set allow-prerelease to use it)"


def build_plan(occurrences: list[Occurrence], catalog: Catalog, config: Config, today: dt.date) -> Plan:
    changes: list[Change] = []
    findings: list[Finding] = []
    cache: dict[tuple[str, int], list[Candidate] | str] = {}

    for occ in occurrences:
        hit = catalog.lookup(occ.text)
        rule = config.rule_for(occ.path)
        if hit is None or rule is None:
            continue
        model, slug = hit
        life = model.lifecycle(today)

        if any(
            r.blocks(occ.path, occ.text, None, None) for r in config.ignore if not (r.target or r.update_types)
        ):
            findings.append(Finding(occ, model, "info", "ignored by configuration"))
            continue

        key = (model.key, id(rule))
        if key not in cache:
            try:
                cache[key] = _candidates(model, rule, catalog, config, today)
            except NoCandidates as e:
                cache[key] = str(e)
        candidates = cache[key]
        reason = candidates if isinstance(candidates, str) else None
        candidates = [] if reason else candidates

        unrenderable: list[str] = []
        weights = rule.cost_optimized.price_weights
        change = _first_change(occ, model, slug, candidates, rule.strategy, config, unrenderable, weights)

        if change is None and rule.strategy == "same-family" and "snapshot" in rule.same_family.update_types:
            target = _snapshot_bump(model, slug)
            if target and not any(r.blocks(occ.path, occ.text, target.text, "snapshot") for r in config.ignore):
                change = Change(occ, model, slug, model, target, "snapshot", rule.strategy, (), weights)

        if change is None and life != "active" and rule.strategy == "cost-optimized":
            # A retiring model must move somewhere: fall back to its own line.
            fallback = same_family(model, catalog, rule.same_family, today)
            change = _first_change(occ, model, slug, fallback, "same-family", config, unrenderable, weights)
            if change:
                why = reason or "no cheaper model met the benchmark bar"
                change = replace(
                    change,
                    notes=(
                        *change.notes,
                        f"{lifecycle_text(model, today)}; cost-optimized found nothing ({why}), "
                        "so the same-family successor was used.",
                    ),
                )

        if change:
            changes.append(change)
            continue

        if life != "active":
            detail = f": {reason}" if reason else ""
            hint = _prerelease_hint(model, catalog, today) if not rule.same_family.allow_prerelease else ""
            findings.append(
                Finding(occ, model, "warning", f"{lifecycle_text(model, today)}; no replacement found{detail}{hint}")
            )
        elif reason:
            findings.append(Finding(occ, model, "info", reason))
        elif unrenderable:
            label = STYLE_LABELS.get(slug.style, slug.style)
            findings.append(
                Finding(
                    occ,
                    model,
                    "info",
                    f"newer option(s) {', '.join(unrenderable[:3])} have no {label} id matching `{occ.text}`",
                )
            )

    return Plan(occurrences, changes, findings, today)
