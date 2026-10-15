"""Human- and machine-readable output: terminal tables, JSON, PR bodies."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .catalog import STYLE_LABELS, Catalog, Model
from .config import Config
from .planner import Change, Group, Plan, savings, status_text
from .scanner import Occurrence
from .vendors import VENDORS


def _money(v: float | None, markdown: bool = False) -> str:
    if v is None:
        return "–"
    text = f"${v:,.2f}" if v >= 0.1 else f"${v:,.3f}"
    # GitHub can read a pair of dollar signs as inline math.
    return text.replace("$", "\\$") if markdown else text


def _pct(v: float | None) -> str:
    return "" if v is None else f" ({-v:+.0%})"


def _table(rows: list[list[str]], indent: str = "  ") -> list[str]:
    if not rows:
        return []
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [indent + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]


# --- terminal -------------------------------------------------------------------


def scan_text(occurrences: list[Occurrence], catalog: Catalog, today: dt.date) -> str:
    if not occurrences:
        return "No known model slugs found."
    rows = []
    for o in occurrences:
        model, slug = catalog.lookup(o.text)
        rows.append(
            [
                f"{o.path}:{o.line}",
                o.text,
                model.display_name,
                STYLE_LABELS.get(slug.style, slug.style),
                model.lifecycle(today),
            ]
        )
    files = len({o.path for o in occurrences})
    return "\n".join([f"{len(occurrences)} model reference(s) in {files} file(s):", *_table(rows)])


def plan_text(plan: Plan) -> str:
    lines = []
    files = len({o.path for o in plan.occurrences})
    lines.append(
        f"{len(plan.occurrences)} model reference(s) in {files} file(s); {len(plan.changes)} update(s) proposed."
    )
    if plan.changes:
        lines.append("")
        rows = [
            [f"{c.occurrence.path}:{c.occurrence.line}", c.from_slug.text, "->", c.to_slug.text, c.kind, c.strategy]
            for c in plan.changes
        ]
        lines.extend(_table(rows))
    notes = [(c, n) for c in plan.changes for n in c.notes]
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  {c.occurrence.path}:{c.occurrence.line}  {n}" for c, n in notes)
    warnings = [f for f in plan.findings if f.level == "warning"]
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  {f.occurrence.path}:{f.occurrence.line}  {f.occurrence.text}: {f.message}" for f in warnings)
    skipped = _grouped_skips(plan)
    if skipped:
        lines.append("")
        lines.append("Not changed:")
        for message, items in skipped[:15]:
            where = ", ".join(f"{f.occurrence.path}:{f.occurrence.line}" for f in items[:3])
            more = f" and {len(items) - 3} more" if len(items) > 3 else ""
            lines.append(f"  {len(items):>4} x {message}  ({where}{more})")
        if len(skipped) > 15:
            lines.append(f"  ... {len(skipped) - 15} more reason(s); use --json for everything")
    return "\n".join(lines)


def _grouped_skips(plan: Plan) -> list[tuple[str, list]]:
    groups: dict[str, list] = {}
    for f in plan.findings:
        if f.level == "info":
            # "file names 9 different models; ..." -> one bucket per reason
            key = f.message
            for prefix in ("file names ", "in a comment", "in prose", "used as a key", "file already references"):
                if f.message.startswith(prefix):
                    key = prefix.strip() if prefix != "file names " else "file is a model list"
                    break
            groups.setdefault(key, []).append(f)
    return sorted(groups.items(), key=lambda kv: -len(kv[1]))


def annotations(plan: Plan) -> str:
    """GitHub Actions workflow commands: inline notes on the changed lines."""

    def esc(v: str, prop: bool = False) -> str:
        v = v.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        return v.replace(":", "%3A").replace(",", "%2C") if prop else v

    out = []
    for c in plan.changes:
        o = c.occurrence
        msg = f"{c.from_slug.text} can be bumped to {c.to_slug.text} ({c.kind}, {c.strategy})"
        out.append(f"::notice file={esc(o.path, True)},line={o.line},title={esc('ModelBump', True)}::{esc(msg)}")
    for f in plan.findings:
        if f.level == "warning":
            o = f.occurrence
            out.append(f"::warning file={esc(o.path, True)},line={o.line},title=ModelBump::{esc(f.message)}")
    return "\n".join(out)


def summary_markdown(plan: Plan, results: list | None = None) -> str:
    """Markdown for $GITHUB_STEP_SUMMARY."""
    files = len({o.path for o in plan.occurrences})
    parts = [
        "## ModelBump",
        "",
        f"{len(plan.occurrences)} model reference(s) in {files} file(s), "
        f"**{len(plan.changes)} update(s)** proposed.",
        "",
    ]
    groups = plan.groups()
    if groups:
        parts += ["| Update | Kind | References | Files |", "|---|---|---|---|"]
        for g in groups:
            parts.append(
                f"| {g.title()} | {g.kind} | {len(g.changes)} | "
                + ", ".join(f"`{f}`" for f in g.files[:4])
                + (f" +{len(g.files) - 4}" if len(g.files) > 4 else "")
                + " |"
            )
        parts.append("")
    warnings = [f for f in plan.findings if f.level == "warning"]
    if warnings:
        parts.append("**Warnings**")
        parts.append("")
        parts += [f"- `{f.occurrence.path}:{f.occurrence.line}` `{f.occurrence.text}`: {f.message}" for f in warnings]
        parts.append("")
    if results:
        parts += ["**Pull requests**", "", "| Result | Branch | Title |", "|---|---|---|"]
        for r in results:
            link = f"[{r.action}]({r.url})" if r.url else r.action
            detail = f" ({r.detail})" if r.detail else ""
            parts.append(f"| {link}{detail} | `{r.branch}` | {r.title} |")
        parts.append("")
    skipped = _grouped_skips(plan)
    if skipped:
        parts.append("<details><summary>Not changed</summary>\n")
        parts += [f"- {len(items)} x {message}" for message, items in skipped]
        parts.append("\n</details>\n")
    return "\n".join(parts) + "\n"


def catalog_text(models: list[Model], today: dt.date) -> str:
    rows = [["MODEL", "IDENTITY", "RELEASED", "STATUS", "IN/OUT $/1M", "SCORE", "SLUGS"]]
    for m in sorted(models, key=lambda m: (m.vendor, m.identity.family, m.released or dt.date.min)):
        rows.append(
            [
                m.display_name,
                m.key,
                m.released.isoformat() if m.released else "–",
                m.lifecycle(today),
                f"{_money(m.price_in)}/{_money(m.price_out)}",
                "–" if m.score is None else f"{m.score:g}",
                str(len(m.slugs)),
            ]
        )
    return "\n".join(_table(rows, indent=""))


# --- JSON -----------------------------------------------------------------------


def model_json(m: Model, today: dt.date) -> dict[str, Any]:
    return {
        "key": m.key,
        "name": m.display_name,
        "vendor": m.vendor,
        "family": m.identity.family,
        "version": m.identity.version_str,
        "prerelease": m.identity.prerelease,
        "released": m.released.isoformat() if m.released else None,
        "status": m.lifecycle(today),
        "retires": m.retires.isoformat() if m.retires else None,
        "price_input": m.price_in,
        "price_output": m.price_out,
        "context": m.context,
        "open_weights": m.open_weights,
        "score": m.score,
        "score_source": m.score_source,
        "slugs": [
            {"slug": s.text, "style": s.style, "pinned": s.pinned, "modifier": s.modifier, "derived": s.derived}
            for s in m.slugs
        ],
    }


def change_json(c: Change) -> dict[str, Any]:
    o = c.occurrence
    return {
        "path": o.path,
        "line": o.line,
        "column": o.column,
        "from": c.from_slug.text,
        "to": c.to_slug.text,
        "from_model": c.from_model.key,
        "to_model": c.to_model.key,
        "style": c.from_slug.style,
        "kind": c.kind,
        "strategy": c.strategy,
        "notes": list(c.notes),
    }


def plan_json(plan: Plan) -> dict[str, Any]:
    return {
        "date": plan.today.isoformat(),
        "references": len(plan.occurrences),
        "changes": [change_json(c) for c in plan.changes],
        "findings": [
            {
                "path": f.occurrence.path,
                "line": f.occurrence.line,
                "slug": f.occurrence.text,
                "model": f.model.key,
                "level": f.level,
                "message": f.message,
            }
            for f in plan.findings
        ],
    }


# --- pull requests --------------------------------------------------------------


def _subgroups(group: Group) -> list[Group]:
    if group.kind != "multiple":
        return [group]
    return Plan([], group.changes, [], dt.date.min).groups("per-model")


def _comparison(g: Group, catalog: Catalog, config: Config, today: dt.date) -> list[str]:
    weights = g.price_weights
    a, b = g.from_model, g.to_model
    ratio = f"{weights[0]:g}:{weights[1]:g}"
    rows = [
        ("Model", f"{a.display_name}", f"{b.display_name}"),
        ("Released", a.released.isoformat() if a.released else "–", b.released.isoformat() if b.released else "–"),
        ("Status", status_text(a, today), status_text(b, today)),
        (
            "Price per 1M tokens (input / output)",
            f"{_money(a.price_in, True)} / {_money(a.price_out, True)}",
            f"{_money(b.price_in, True)} / {_money(b.price_out, True)}",
        ),
        (
            f"Blended price ({ratio} input:output)",
            _money(a.blended_price(weights), True),
            _money(b.blended_price(weights), True) + _pct(savings(a, b, weights)),
        ),
    ]
    if a.score is not None or b.score is not None:
        source = b.score_source or a.score_source or ""
        rows.append(
            (
                f"Benchmark ({config.catalog.benchmark}{', ' + source if source else ''})",
                "–" if a.score is None else f"{a.score:g}",
                "–" if b.score is None else f"{b.score:g}",
            )
        )
    if a.context or b.context:
        rows.append(("Context window", f"{a.context:,}" if a.context else "–", f"{b.context:,}" if b.context else "–"))
    out = ["| | Current | Proposed |", "|---|---|---|"]
    out.extend(f"| {k} | {x} | {y} |" for k, x, y in rows)
    return out


def _intro(g: Group) -> str:
    if g.kind == "snapshot":
        return f"Bumps `{g.from_slugs[0]}` to the newer snapshot `{g.to_slugs[0]}` ({g.from_model.display_name})."
    if g.strategy == "cost-optimized":
        return (
            f"Switches **{g.from_model.display_name}** to **{g.to_model.display_name}**, which scores at least as "
            f"well on the configured benchmark (within tolerance) for less money."
        )
    return (
        f"Bumps **{g.from_model.display_name}** to **{g.to_model.display_name}** "
        f"({g.kind} update within the same model line)."
    )


def pr_body(group: Group, catalog: Catalog, config: Config, today: dt.date) -> str:
    parts: list[str] = []
    subgroups = _subgroups(group)
    if len(subgroups) > 1:
        parts.append(f"Updates {len(subgroups)} hard-coded model references.\n")
    for g in subgroups:
        if len(subgroups) > 1:
            parts.append(f"## {g.title()}\n")
        parts.append(_intro(g) + "\n")
        if g.kind != "snapshot" or g.from_model is not g.to_model:
            parts.extend(_comparison(g, catalog, config, today))
            parts.append("")
        parts.append("**Changed references**\n")
        parts.append("| Location | From | To | Format |")
        parts.append("|---|---|---|---|")
        for c in g.changes:
            o = c.occurrence
            parts.append(
                f"| `{o.path}:{o.line}` | `{c.from_slug.text}` | `{c.to_slug.text}` "
                f"| {STYLE_LABELS.get(c.from_slug.style, c.from_slug.style)} |"
            )
        parts.append("")
        notes = list(dict.fromkeys(n for c in g.changes for n in c.notes))
        vendor = VENDORS[g.to_model.vendor]
        if g.kind in ("major", "switch") or g.to_model.vendor == "anthropic":
            advice = "A newer model can change output style, tool-use behaviour, and accepted API parameters."
            if vendor.migration_url:
                advice += f" Check the [{vendor.name} migration notes]({vendor.migration_url}) and run your evals before merging."
            else:
                advice += " Run your evals before merging."
            notes.append(advice)
        if notes:
            parts.append("**Notes**\n")
            parts.extend(f"- {n}" for n in notes)
            parts.append("")
    sources = ", ".join(f"{k}: {v}" for k, v in catalog.origins.items())
    from_slug = group.from_slugs[0]
    parts.append(f"<sub>Catalog sources — {sources}</sub>\n")
    parts.append("---")
    parts.append(
        "<sub>Opened by [ModelBump](https://github.com/cheesejaguar/ModelBump). ModelBump rebases this "
        "branch when its edits change and closes the PR when a newer update supersedes it. Pushing your "
        "own commits here stops ModelBump from touching the branch; closing the PR skips this update. "
        "To skip this model entirely, add to your ModelBump config:</sub>\n"
    )
    parts.append(f'```yaml\nignore:\n  - model: "{from_slug}"\n```')
    parts.append(f"<!-- modelbump:{group.from_model.key}->{group.to_model.key} -->")
    return "\n".join(parts)
