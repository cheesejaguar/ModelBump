"""LS-126: Shabbat guard and cron acceptance tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shabbat_guard_edges_pst_and_pdt() -> None:
    subprocess.check_call(["bash", str(ROOT / ".github/shabbat/test_guard.sh")])


def test_scheduled_crons_outside_shabbat_window() -> None:
    subprocess.check_call([sys.executable, str(ROOT / ".github/shabbat/test_crons.py")])
