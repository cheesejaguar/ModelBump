import datetime as dt
import subprocess

import pytest

from modelbump.catalog import Catalog, load_bundled_seed, load_openrouter
from modelbump.sources import bundled_openrouter

TODAY = dt.date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path_factory):
    """No network credentials and a private cache for every test."""
    for var in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_PROFILE",
        "OPENAI_API_KEY",
        "ARTIFICIAL_ANALYSIS_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MODELBUMP_CACHE_DIR", str(tmp_path_factory.mktemp("cache")))


@pytest.fixture(scope="session")
def _base_catalog_data():
    return bundled_openrouter()["data"]


@pytest.fixture
def catalog(_base_catalog_data) -> Catalog:
    cat = Catalog()
    load_bundled_seed(cat)
    load_openrouter(cat, _base_catalog_data)
    return cat


@pytest.fixture
def today() -> dt.date:
    return TODAY


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """An empty git repository with a committer identity."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")
    return path
