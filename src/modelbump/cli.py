"""Command-line interface."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from importlib import resources
from pathlib import Path

from . import __version__
from .catalog import Catalog, build_catalog
from .config import Config, ConfigError, load_config
from .editor import StaleFileError, apply_changes, render_diff
from .github import GitHubError, open_pull_requests
from .planner import Plan, build_plan
from .report import catalog_text, model_json, plan_json, plan_text, scan_text
from .scanner import scan

log = logging.getLogger("modelbump")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-C", "--root", type=Path, default=Path("."), help="repository root (default: .)")
    p.add_argument("--config", type=Path, help="config file (default: .github/modelbump.yml if present)")
    p.add_argument(
        "--strategy", choices=("same-family", "cost-optimized"), help="override the strategy for every path"
    )
    p.add_argument("--offline", action="store_true", help="use cached or bundled catalog data only")
    p.add_argument("--refresh", action="store_true", help="ignore cached catalog data")
    p.add_argument("--today", type=dt.date.fromisoformat, help=argparse.SUPPRESS)
    p.add_argument("-v", "--verbose", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modelbump", description="Dependabot-style updates for hard-coded LLM model slugs."
    )
    parser.add_argument("--version", action="version", version=f"modelbump {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="list model slugs found in the repository")
    _common(p)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("plan", help="show the updates ModelBump would make (no changes)")
    _common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--diff", action="store_true", help="print a unified diff of the proposed edits")
    p.add_argument("--fail-on-updates", action="store_true", help="exit with status 2 when updates are available")

    p = sub.add_parser("apply", help="rewrite files in the working tree")
    _common(p)
    p.add_argument("--diff", action="store_true", help="also print the diff")

    p = sub.add_parser("pr", help="open or refresh one pull request per update (needs git and gh)")
    _common(p)
    p.add_argument("--base", help="base branch (default: the checked-out branch)")
    p.add_argument("--dry-run", action="store_true", help="list the pull requests without pushing")

    p = sub.add_parser("catalog", help="show the model catalog")
    _common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--vendor", help="only this vendor, e.g. anthropic")
    p.add_argument("--search", help="only models whose name, identity or slugs contain this text")

    p = sub.add_parser("init", help="write a starter .github/modelbump.yml")
    p.add_argument("-C", "--root", type=Path, default=Path("."))
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    return parser


def _load(args) -> tuple[Path, Config, Catalog, dt.date]:
    root = args.root.resolve()
    config = load_config(root, args.config)
    if args.strategy:
        config = config.with_strategy(args.strategy)
    catalog = build_catalog(config.catalog, root=root, offline=args.offline, refresh=args.refresh)
    for note in catalog.notes:
        log.warning(note)
    return root, config, catalog, args.today or dt.date.today()


def _plan(args) -> tuple[Path, Config, Catalog, Plan]:
    root, config, catalog, today = _load(args)
    occurrences = scan(root, catalog, config)
    return root, config, catalog, build_plan(occurrences, catalog, config, today)


def cmd_scan(args) -> int:
    root, config, catalog, today = _load(args)
    occurrences = scan(root, catalog, config)
    if args.json:
        rows = []
        for o in occurrences:
            model, slug = catalog.lookup(o.text)
            rows.append(
                {
                    "path": o.path,
                    "line": o.line,
                    "column": o.column,
                    "slug": o.text,
                    "style": slug.style,
                    "model": model_json(model, today),
                }
            )
        print(json.dumps(rows, indent=2))
    else:
        print(scan_text(occurrences, catalog, today))
    return 0


def cmd_plan(args) -> int:
    root, config, catalog, plan = _plan(args)
    if args.json:
        print(json.dumps(plan_json(plan), indent=2))
    else:
        print(plan_text(plan))
        if args.diff and plan.changes:
            print()
            print(render_diff(root, plan.changes), end="")
    return 2 if args.fail_on_updates and plan.changes else 0


def cmd_apply(args) -> int:
    root, config, catalog, plan = _plan(args)
    print(plan_text(plan))
    if not plan.changes:
        return 0
    if args.diff:
        print()
        print(render_diff(root, plan.changes), end="")
    edited = apply_changes(root, plan.changes)
    print(f"\nUpdated {len(plan.changes)} reference(s) in {len(edited)} file(s).")
    return 0


def cmd_pr(args) -> int:
    root, config, catalog, plan = _plan(args)
    print(plan_text(plan))
    results = open_pull_requests(
        plan, root, catalog, config, args.today or dt.date.today(), base=args.base, dry_run=args.dry_run
    )
    if results:
        print()
        for r in results:
            extra = f"  {r.url}" if r.url else ""
            detail = f"  ({r.detail})" if r.detail else ""
            print(f"{r.action:<13} {r.branch}  {r.title}{extra}{detail}")
    return 0


def cmd_catalog(args) -> int:
    root, config, catalog, today = _load(args)
    models = list(catalog.models.values())
    if args.vendor:
        models = [m for m in models if m.vendor == args.vendor]
    if args.search:
        needle = args.search.lower()
        models = [
            m
            for m in models
            if needle in m.display_name.lower()
            or needle in m.key.lower()
            or any(needle in s.text.lower() for s in m.slugs)
        ]
    if args.json:
        print(json.dumps([model_json(m, today) for m in models], indent=2))
    else:
        print(catalog_text(models, today))
        print()
        print("Sources: " + "; ".join(f"{k}: {v}" for k, v in catalog.origins.items()))
    return 0


def cmd_init(args) -> int:
    target = args.root.resolve() / ".github" / "modelbump.yml"
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        resources.files("modelbump").joinpath("data/default_config.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    print(f"Wrote {target}")
    return 0


COMMANDS = {
    "scan": cmd_scan,
    "plan": cmd_plan,
    "apply": cmd_apply,
    "pr": cmd_pr,
    "catalog": cmd_catalog,
    "init": cmd_init,
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING,
        format="modelbump: %(levelname)s: %(message)s",
    )
    try:
        return COMMANDS[args.command](args)
    except (ConfigError, GitHubError, StaleFileError) as e:
        print(f"modelbump: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
