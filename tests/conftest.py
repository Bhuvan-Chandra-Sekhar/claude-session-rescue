"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `pytest` to run straight from a checkout without installing first.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests import fixtures  # noqa: E402  (path set up above)


@pytest.fixture
def simple(tmp_path):
    return fixtures.simple_session(tmp_path / "simple.jsonl")


@pytest.fixture
def compacted(tmp_path):
    return fixtures.compacted_session(tmp_path / "compacted.jsonl")


@pytest.fixture
def twice_compacted(tmp_path):
    return fixtures.twice_compacted_session(tmp_path / "twice.jsonl")


@pytest.fixture
def duplicates(tmp_path):
    return fixtures.duplicate_uuid_session(tmp_path / "duplicates.jsonl")


@pytest.fixture
def malformed(tmp_path):
    return fixtures.malformed_session(tmp_path / "malformed.jsonl")


@pytest.fixture
def store_dir(tmp_path):
    return fixtures.make_store(tmp_path / "claude")
