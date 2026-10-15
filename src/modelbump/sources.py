"""Fetch live model data, with an on-disk cache and an offline fallback.

* OpenRouter ``/api/v1/models`` -- public, no key. Model list, pricing,
  release dates, context windows, Hugging Face ids, expiry dates.
* Artificial Analysis ``/api/v2/data/llms/models`` -- needs
  ``ARTIFICIAL_ANALYSIS_API_KEY``. Benchmark scores for cost-optimized mode.
* Anthropic Models API -- needs the ``anthropic`` package and credentials.
  Confirms first-party Claude ids.
* OpenAI ``/v1/models`` -- needs ``OPENAI_API_KEY``. Confirms first-party ids.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("modelbump")

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
ARTIFICIAL_ANALYSIS_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
USER_AGENT = "modelbump/0.1 (+https://github.com/cheesejaguar/ModelBump)"


def cache_dir() -> Path:
    if os.environ.get("MODELBUMP_CACHE_DIR"):
        return Path(os.environ["MODELBUMP_CACHE_DIR"])
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "modelbump"


def http_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URLs
        return json.load(resp)


def cached_fetch(
    name: str,
    fetch: Callable[[], Any],
    *,
    ttl_hours: float,
    refresh: bool = False,
    offline: bool = False,
) -> tuple[Any | None, str]:
    """Return (data, origin): fresh cache, else a live fetch, else stale cache.

    origin is "cache", "live", "stale-cache" or "none".
    """
    path = cache_dir() / f"{name}.json"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600
    if path.exists() and (offline or (fresh and not refresh)):
        try:
            return json.loads(path.read_text(encoding="utf-8")), "cache"
        except (OSError, json.JSONDecodeError):
            log.warning("ignoring unreadable cache file %s", path)
    if offline:
        return None, "none"
    try:
        data = fetch()
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("could not fetch %s (%s)", name, e)
        if path.exists():
            log.warning("using stale cached %s data", name)
            try:
                return json.loads(path.read_text(encoding="utf-8")), "stale-cache"
            except (OSError, json.JSONDecodeError):
                return None, "none"
        return None, "none"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as e:
        log.debug("could not write cache %s: %s", path, e)
    return data, "live"


def slim_openrouter(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the fields ModelBump reads (for the bundled snapshot)."""
    out = []
    for m in records:
        arch = m.get("architecture") or {}
        pricing = m.get("pricing") or {}
        out.append(
            {
                "id": m["id"],
                "name": m.get("name"),
                "created": m.get("created"),
                "hugging_face_id": m.get("hugging_face_id") or None,
                "context_length": m.get("context_length"),
                "pricing": {k: pricing.get(k) for k in ("prompt", "completion")},
                "architecture": {
                    "input_modalities": arch.get("input_modalities"),
                    "output_modalities": arch.get("output_modalities"),
                },
                "expiration_date": m.get("expiration_date"),
            }
        )
    return sorted(out, key=lambda m: m["id"])


def bundled_openrouter() -> dict[str, Any]:
    text = resources.files("modelbump").joinpath("data/openrouter_snapshot.json").read_text(encoding="utf-8")
    return json.loads(text)


def openrouter_models(*, ttl_hours: float, refresh: bool, offline: bool) -> tuple[list[dict[str, Any]], str]:
    """Return (records, origin) where origin says which data was used."""
    data, origin = cached_fetch(
        "openrouter", lambda: http_json(OPENROUTER_URL), ttl_hours=ttl_hours, refresh=refresh, offline=offline
    )
    if data and isinstance(data.get("data"), list):
        return data["data"], f"openrouter.ai ({origin})"
    snap = bundled_openrouter()
    log.info("using bundled OpenRouter snapshot from %s", snap.get("fetched_at", "unknown date"))
    return snap["data"], f"bundled snapshot ({snap.get('fetched_at', '?')[:10]})"


def artificial_analysis_models(*, ttl_hours: float, refresh: bool, offline: bool) -> list[dict[str, Any]] | None:
    key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY")
    if not key:
        return None
    data, _ = cached_fetch(
        "artificial-analysis",
        lambda: http_json(ARTIFICIAL_ANALYSIS_URL, {"x-api-key": key}),
        ttl_hours=ttl_hours,
        refresh=refresh,
        offline=offline,
    )
    if not data or not isinstance(data.get("data"), list):
        return None
    return data["data"]


def anthropic_models(*, ttl_hours: float, refresh: bool, offline: bool) -> list[tuple[str, dt.date | None]] | None:
    if not any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
        return None
    try:
        import anthropic  # optional dependency: pip install "modelbump[anthropic]"
    except ImportError:
        log.info("anthropic package not installed; skipping live Claude model list")
        return None

    def fetch() -> list[list[str | None]]:
        client = anthropic.Anthropic()
        out = []
        for m in client.models.list():
            created = getattr(m, "created_at", None)
            out.append([m.id, created.date().isoformat() if created else None])
        return out

    try:
        rows, _ = cached_fetch("anthropic", fetch, ttl_hours=ttl_hours, refresh=refresh, offline=offline)
    except anthropic.APIError as e:
        log.warning("could not list Anthropic models (%s)", e)
        return None
    if not rows:
        return None
    return [(r[0], dt.date.fromisoformat(r[1]) if r[1] else None) for r in rows]


def openai_models(*, ttl_hours: float, refresh: bool, offline: bool) -> list[tuple[str, dt.date | None]] | None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    data, _ = cached_fetch(
        "openai",
        lambda: http_json(OPENAI_MODELS_URL, {"Authorization": f"Bearer {key}"}),
        ttl_hours=ttl_hours,
        refresh=refresh,
        offline=offline,
    )
    if not data or not isinstance(data.get("data"), list):
        return None
    out = []
    for m in data["data"]:
        created = m.get("created")
        out.append((m["id"], dt.datetime.fromtimestamp(created, dt.timezone.utc).date() if created else None))
    return out
