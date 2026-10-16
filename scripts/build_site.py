"""Build the GitHub Pages site: copy site/ and export catalog data for the demo.

The demo in the browser does not reimplement ModelBump's decisions. For every
slug in the catalog, this script runs the real planner with the default
configuration and records the result, so the page shows exactly what the
action would propose today.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

from modelbump import __version__
from modelbump.catalog import STYLE_LABELS, build_catalog
from modelbump.config import parse_config
from modelbump.planner import build_plan
from modelbump.scanner import Occurrence
from modelbump.vendors import VENDORS, default_ecosystems

ROOT = Path(__file__).resolve().parent.parent

# Hero examples, in order of preference; ones without a bump today are dropped.
HERO = [
    "claude-opus-5",
    "openai/gpt-5-mini",
    "anthropic/claude-sonnet-5",
    "deepseek/deepseek-v4-flash",
    "gemini-2.5-flash",
    "x-ai/grok-4.3",
    "gpt-4o-2024-08-06",
    "moonshotai/kimi-k2.5",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "z-ai/glm-5.2",
]


def export(out: Path, *, offline: bool) -> dict:
    config = parse_config({})
    catalog = build_catalog(config.catalog, offline=offline)
    today = dt.date.today()
    weights = config.rules[0].cost_optimized.price_weights

    models = {}
    for m in catalog.models.values():
        models[m.key] = {
            "name": m.display_name,
            "vendor": m.vendor,
            "family": m.identity.family,
            "version": m.identity.version_str,
            "released": m.released.isoformat() if m.released else None,
            "status": m.lifecycle(today),
            "retires": m.retires.isoformat() if m.retires else None,
            "in": m.price_in,
            "out": m.price_out,
            "blended": m.blended_price(weights),
            "open": m.open_weights,
        }

    slugs = {}
    for text, (model, slug) in sorted(catalog.index().items()):
        occ = Occurrence("demo.py", 1, 2, 1, 1 + len(text), text, f'"{text}"')
        plan = build_plan([occ], catalog, config, today)
        entry = {"m": model.key, "s": slug.style}
        if plan.changes:
            c = plan.changes[0]
            entry.update({"to": c.to_slug.text, "tm": c.to_model.key, "k": c.kind, "n": list(c.notes)})
        elif plan.findings:
            entry["why"] = plan.findings[0].message
        slugs[text] = entry

    hero = [[s, slugs[s]["to"]] for s in HERO if s in slugs and "to" in slugs[s]]
    vendors = []
    for v in VENDORS.values():
        count = sum(1 for m in catalog.models.values() if m.vendor == v.key)
        vendors.append({"key": v.key, "name": v.name, "ecosystem": v.ecosystem, "models": count})

    data = {
        "version": __version__,
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "sources": catalog.origins,
        "stats": {
            "models": len(catalog.models),
            "slugs": len(slugs),
            "vendors": sum(1 for v in vendors if v["models"]),
        },
        "styles": STYLE_LABELS,
        "ecosystems": default_ecosystems(),
        "vendors": vendors,
        "hero": hero,
        "models": models,
        "slugs": slugs,
    }
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "data" / "catalog.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return data


def fingerprint(out: Path) -> None:
    """Version asset URLs by content so browsers never mix old and new files."""
    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    for name in ("styles.css", "app.js"):
        digest = hashlib.sha256((out / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'"{name}"', f'"{name}?v={digest}"')
    index.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "_site")
    parser.add_argument("--offline", action="store_true", help="use cached or bundled catalog data")
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    shutil.copytree(ROOT / "site", args.out)
    (args.out / ".nojekyll").write_text("")
    fingerprint(args.out)
    data = export(args.out, offline=args.offline)
    print(
        f"built {args.out}: {data['stats']['models']} models, {data['stats']['slugs']} slugs, "
        f"{len(data['hero'])} hero examples"
    )


if __name__ == "__main__":
    main()
