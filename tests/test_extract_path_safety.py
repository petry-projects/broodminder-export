"""Path-traversal guard tests for scripts/extract_all.py (pythonsecurity:S8707).

`extract_all.py` writes raw data to `<out>/raw/<hiveId>/<start>-<end>...`. Both the
`--out` directory (a CLI argument) and the `hiveId` (external API data) are
untrusted: a component containing `..` or an absolute path could otherwise escape
the extract directory and clobber arbitrary files. `resolve_within` is the
containment validator that fails closed on any such escape; these tests pin that
behavior so the guard can't silently regress.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "extract_all", ROOT / "scripts" / "extract_all.py"
)
assert _SPEC is not None and _SPEC.loader is not None, "extract_all spec/loader not found"
extract_all = importlib.util.module_from_spec(_SPEC)
_sys_path_snapshot = sys.path[:]
try:
    _SPEC.loader.exec_module(extract_all)
finally:
    sys.path[:] = _sys_path_snapshot
resolve_within = extract_all.resolve_within


def test_normal_component_stays_within(tmp_path):
    base = tmp_path / "raw"
    got = resolve_within(base, "hive-123")
    assert got == (base / "hive-123").resolve()
    assert got.is_relative_to(base.resolve())


def test_nested_window_filename_allowed(tmp_path):
    """The real call shape: a per-hive dir plus a window-keyed filename."""
    hdir = resolve_within(tmp_path / "raw", "AA:BB:CC")
    got = resolve_within(hdir, "1609459200-1625097600.readings.json.gz")
    assert got.is_relative_to((tmp_path / "raw").resolve())


def test_no_parts_returns_base(tmp_path):
    assert resolve_within(tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize(
    "evil",
    [
        "..",
        "../escape",
        "../../etc/passwd",
        "a/../../../escape",
    ],
)
def test_parent_traversal_rejected(tmp_path, evil):
    with pytest.raises(ValueError):
        resolve_within(tmp_path / "raw", evil)


def test_absolute_component_rejected(tmp_path):
    with pytest.raises(ValueError):
        resolve_within(tmp_path / "raw", "/etc/passwd")


def test_embedded_traversal_in_hive_id_rejected(tmp_path):
    """A hostile hiveId must not be able to redirect the write outside the root."""
    raw = tmp_path / "raw"
    with pytest.raises(ValueError):
        resolve_within(raw, "../../../../tmp/pwned")
