"""Choose replacement models.

same-family
    Newer releases of the same line from the same vendor: Opus -> Opus,
    gpt-mini -> gpt-mini, qwen-max -> qwen-max. ``update-types`` limits how
    far a bump may go (snapshot, minor, major), like Dependabot's semver rules.

cost-optimized
    Models in the same ecosystem whose benchmark score is at least the
    current score (minus a tolerance) and whose blended price is lower.
    Ecosystems keep Claude with Claude, OpenAI with OpenAI, and Chinese
    open-weight models with Chinese open-weight models.

Both return candidates best-first; the planner takes the first one that can
be written in the same format (host, alias vs dated) as the original slug.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .catalog import Catalog, Model
from .config import Config, CostOptimized, SameFamily
from .slugs import update_type


@dataclass(frozen=True)
class Candidate:
    model: Model
    kind: str  # snapshot | minor | major | switch
    note: str | None = None


class NoCandidates(Exception):
    """Raised with a human-readable reason when a strategy cannot run."""


def _newness(m: Model) -> tuple:
    return (m.released or dt.date.min, m.identity.version, not m.identity.prerelease)


def is_newer(a: Model, b: Model) -> bool:
    """Release date decides when both are known; versions decide otherwise.

    Dates matter because vendors do not number monotonically: grok-4.20 came
    out before grok-4.3.
    """
    if a.released and b.released and a.released != b.released:
        return a.released > b.released
    return (a.identity.version, not a.identity.prerelease) > (b.identity.version, not b.identity.prerelease)


def same_family(
    model: Model,
    catalog: Catalog,
    policy: SameFamily,
    today: dt.date,
    weights: tuple[float, float] = (3.0, 1.0),
) -> list[Candidate]:
    out = []
    too_expensive: list[tuple[Model, float]] = []
    current_price = model.blended_price(weights)
    for m in catalog.family(model):
        if m is model or m.lifecycle(today) != "active":
            continue
        if m.identity.prerelease and not (policy.allow_prerelease or model.identity.prerelease):
            continue
        if not is_newer(m, model):
            continue
        kind = update_type(model.identity, m.identity)
        if kind not in policy.update_types:
            continue
        price = m.blended_price(weights)
        if policy.max_price_increase and current_price and price is not None:
            if price > current_price * policy.max_price_increase:
                too_expensive.append((m, price / current_price))
                continue
        out.append(Candidate(m, kind))
    out.sort(key=lambda c: _newness(c.model), reverse=True)
    if too_expensive:
        m, ratio = max(too_expensive, key=lambda mr: _newness(mr[0]))
        why = (
            f"{m.display_name} costs {ratio:.1f}x as much as {model.display_name} "
            f"(same-family.max-price-increase is {policy.max_price_increase:g})"
        )
        if not out:
            raise NoCandidates(why)
        out = [Candidate(c.model, c.kind, f"Skipped the newer {why}.") if is_newer(m, c.model) else c for c in out]
    return out


def cost_optimized(
    model: Model, catalog: Catalog, policy: CostOptimized, config: Config, today: dt.date
) -> list[Candidate]:
    if model.score is None:
        raise NoCandidates(
            f"no benchmark score for {model.display_name}; set ARTIFICIAL_ANALYSIS_API_KEY "
            "or add one under catalog.scores"
        )
    current_price = model.blended_price(policy.price_weights)
    if current_price is None:
        raise NoCandidates(f"no price known for {model.display_name}")

    if policy.scope == "vendor":
        vendors = [model.vendor]
    else:
        vendors = config.ecosystems.get(config.ecosystem_of(model.vendor), [model.vendor])
    max_price = current_price * (1 - policy.min_savings)
    min_score = model.score * (1 - policy.max_score_drop)

    pool = []
    for m in catalog.by_vendors(vendors):
        if m is model or m.score is None or m.lifecycle(today) != "active":
            continue
        if m.identity.prerelease and not policy.allow_prerelease:
            continue
        price = m.blended_price(policy.price_weights)
        if price is None or price > max_price or m.score < min_score:
            continue
        if policy.preserve_open_weights and model.open_weights and not m.open_weights:
            continue
        if not policy.allow_older and model.released and m.released and m.released < model.released:
            continue
        if model.input_modalities and m.input_modalities and not model.input_modalities <= m.input_modalities:
            continue
        if model.output_modalities and m.output_modalities and model.output_modalities != m.output_modalities:
            continue
        if policy.require_context and model.context and m.context and m.context < model.context:
            continue
        pool.append((m, price))

    if policy.prefer == "best-score":
        pool.sort(key=lambda mp: (-mp[0].score, mp[1], -(mp[0].released or dt.date.min).toordinal()))
    else:
        pool.sort(key=lambda mp: (mp[1], -mp[0].score, -(mp[0].released or dt.date.min).toordinal()))
    return [Candidate(m, update_type(model.identity, m.identity)) for m, _ in pool]
