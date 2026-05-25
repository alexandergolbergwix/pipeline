"""Unit tests for :mod:`scripts.bump_patch_version` (Rule 51)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


def _load_bumper() -> object:
    """Load the bumper as a module so we can test its public functions
    without invoking the CLI surface."""
    here = Path(__file__).resolve().parents[2] / "scripts" / "bump_patch_version.py"
    spec = importlib.util.spec_from_file_location("_bump", here)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bumper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> object:
    mod = _load_bumper()
    fake = tmp_path / "pyproject.toml"
    fake.write_text('[project]\nname = "test"\nversion = "1.2.3"\n', encoding="utf-8")
    monkeypatch.setattr(mod, "PYPROJECT", fake)
    return mod


class TestBumpPatch:
    def test_increment_writes_new_version(self, bumper: object) -> None:
        rc = bumper.main([])
        assert rc == 0
        text = bumper.PYPROJECT.read_text(encoding="utf-8")
        assert 'version = "1.2.4"' in text

    def test_check_does_not_write(self, bumper: object) -> None:
        rc = bumper.main(["--check"])
        assert rc == 0
        text = bumper.PYPROJECT.read_text(encoding="utf-8")
        assert 'version = "1.2.3"' in text  # untouched

    def test_target_pins_exact_version(self, bumper: object) -> None:
        rc = bumper.main(["--target", "2.0.0"])
        assert rc == 0
        assert 'version = "2.0.0"' in bumper.PYPROJECT.read_text(encoding="utf-8")

    def test_target_rejects_non_semver(self, bumper: object) -> None:
        rc = bumper.main(["--target", "2.0"])
        assert rc != 0

    def test_only_modifies_version_line(self, bumper: object) -> None:
        bumper.PYPROJECT.write_text(
            '[project]\n'
            'name = "test"\n'
            'version = "0.1.0"\n'
            'dependencies = ["pkg>=0.1.0"]\n',
            encoding="utf-8",
        )
        bumper.main([])
        text = bumper.PYPROJECT.read_text(encoding="utf-8")
        # dependency pin must NOT be touched.
        assert 'dependencies = ["pkg>=0.1.0"]' in text
        # version line was bumped.
        assert 'version = "0.1.1"' in text

    def test_unparsable_pyproject_raises(self, bumper: object) -> None:
        bumper.PYPROJECT.write_text(
            '[project]\nname = "test"\n',  # no version at all
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="Could not find"):
            bumper._parse_current(bumper.PYPROJECT.read_text(encoding="utf-8"))
