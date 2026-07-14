"""Path-confinement tests for scripts/extract_all.py (SonarCloud S8707).

The extractor builds on-disk paths from untrusted input: the ``--out`` CLI
argument and the API-supplied ``hiveId``. A crafted id containing ``..`` or an
absolute path must not steer writes outside the chosen output tree. These tests
pin the ``safe_subdir`` traversal guard that enforces that invariant.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "extract_all.py"

_spec = importlib.util.spec_from_file_location("extract_all", SCRIPT)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load {SCRIPT}")
extract_all = importlib.util.module_from_spec(_spec)
_saved_path = sys.path.copy()
try:
    _spec.loader.exec_module(extract_all)
finally:
    sys.path[:] = _saved_path


def test_safe_subdir_allows_normal_id(tmp_path):
    """A plain hive id resolves to base/id, unchanged behavior."""
    got = extract_all.safe_subdir(tmp_path, "48-0004AABB")
    assert got == (tmp_path / "48-0004AABB").resolve()
    assert got.parent == tmp_path.resolve()


def test_safe_subdir_rejects_parent_traversal(tmp_path):
    """A ../-laden id must raise rather than escape the base directory."""
    with pytest.raises(ValueError):
        extract_all.safe_subdir(tmp_path, "../../etc/evil")


def test_safe_subdir_rejects_absolute_path(tmp_path):
    """An absolute-path id (which would reset the join) must be rejected."""
    with pytest.raises(ValueError):
        extract_all.safe_subdir(tmp_path, "/etc/passwd")


def test_safe_subdir_confines_within_base(tmp_path):
    """Any accepted result is always inside the base tree."""
    got = extract_all.safe_subdir(tmp_path, "nested-id")
    assert got.is_relative_to(tmp_path.resolve())


def test_safe_subdir_rejects_dot_name(tmp_path):
    """A '.' name resolves to base itself, which must be rejected as a strict subdirectory is required."""
    with pytest.raises(ValueError):
        extract_all.safe_subdir(tmp_path, ".")


def test_safe_subdir_rejects_empty_name(tmp_path):
    """An empty name resolves to base itself, which must be rejected."""
    with pytest.raises(ValueError):
        extract_all.safe_subdir(tmp_path, "")
