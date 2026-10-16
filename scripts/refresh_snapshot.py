"""Refresh src/modelbump/data/openrouter_snapshot.json from OpenRouter."""

import datetime as dt
import json
from pathlib import Path

from modelbump.sources import OPENROUTER_URL, http_json, slim_openrouter

TARGET = Path(__file__).resolve().parent.parent / "src/modelbump/data/openrouter_snapshot.json"


def main() -> None:
    records = slim_openrouter(http_json(OPENROUTER_URL)["data"])
    old = json.loads(TARGET.read_text()) if TARGET.exists() else {}
    if old.get("data") == records:
        print("unchanged")
        return
    snapshot = {
        "source": OPENROUTER_URL,
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": records,
    }
    TARGET.write_text(json.dumps(snapshot, separators=(",", ":")) + "\n")
    print(f"wrote {len(records)} models to {TARGET}")


if __name__ == "__main__":
    main()
