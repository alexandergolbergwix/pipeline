"""Unit tests for Rule 36 theme-token usage in the review popups.

Three surfaces are covered:

* :func:`mhm_pipeline.gui.widgets.glass_dialog.glass_tab_style` — must be
  theme-branched (different output for dark vs light) and must source its
  tab text colour from ``theme.ui("text"/"subtext")``.
* :class:`mhm_pipeline.gui.widgets.marc_evidence_popup.MarcEvidencePopup` —
  the header/chip HTML must reference ``theme.ui("success"/"warning"/"info")``
  rather than the literal tailwind hex strings.
* :class:`mhm_pipeline.gui.widgets.match_comparison_dialog.MatchComparisonDialog`
  — the APPROVED badge and warning banner must derive from
  ``theme.ui("success")`` / ``theme.ui("warning")`` and never carry the
  hardcoded ``rgba(34,197,94`` / ``rgba(245, 158, 11`` literals.

Both popups must also construct without error under both themes.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui import theme  # noqa: E402
from mhm_pipeline.gui.widgets import glass_dialog  # noqa: E402
from mhm_pipeline.gui.widgets.match_comparison_dialog import (  # noqa: E402
    MatchComparisonDialog,
)
from mhm_pipeline.gui.widgets.marc_evidence_popup import (  # noqa: E402
    MarcEvidencePopup,
)


@pytest.fixture(scope="module", autouse=True)
def _qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _restore_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test patches ``theme.is_dark``; ensure the cache is fresh."""
    theme.invalidate_cache()
    yield
    theme.invalidate_cache()


def _force_dark(monkeypatch: pytest.MonkeyPatch, dark: bool) -> None:
    monkeypatch.setattr(theme, "is_dark", lambda: dark)


# ── glass_tab_style ────────────────────────────────────────────────────────


def test_glass_tab_style_differs_between_themes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_dark(monkeypatch, True)
    dark = glass_dialog.glass_tab_style(theme)
    _force_dark(monkeypatch, False)
    light = glass_dialog.glass_tab_style(theme)
    assert dark != light, "glass_tab_style must be theme-branched"


def test_glass_tab_style_uses_text_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for dark in (True, False):
        _force_dark(monkeypatch, dark)
        qss = glass_dialog.glass_tab_style(theme)
        assert theme.ui("subtext") in qss
        assert theme.ui("text") in qss


def test_glass_tab_style_light_pane_not_dark_glass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_dark(monkeypatch, False)
    light = glass_dialog.glass_tab_style(theme)
    # Light theme must NOT paint the pane with the dark-glass fill.
    assert "rgba(0,0,0, 75)" not in light
    assert "rgba(255,255,255, 140)" in light


# ── marc_evidence_popup ────────────────────────────────────────────────────


def _build_popup(monkeypatch: pytest.MonkeyPatch, dark: bool) -> MarcEvidencePopup:
    _force_dark(monkeypatch, dark)
    return MarcEvidencePopup(
        needle="משה בן מימון",
        exists_in=[{"field": "authors[0].name", "match_type": "full"}],
        marc_record={"_control_number": "990001", "authors": [{"name": "משה בן מימון"}]},
        role_fields=["authors"],
        grounded=True,
    )


def test_marc_popup_header_uses_success_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popup = _build_popup(monkeypatch, dark=True)
    header = popup._build_header()
    # Find the summary QLabel rich text — it carries the colored dots.
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415
    html = " ".join(
        lbl.text() for lbl in header.findChildren(QLabel)
    )
    assert theme.ui("success") in html
    assert theme.ui("warning") in html


def test_marc_popup_has_no_hardcoded_status_hex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popup = _build_popup(monkeypatch, dark=True)
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415
    header = popup._build_header()
    html = " ".join(lbl.text() for lbl in header.findChildren(QLabel))
    for literal in ("#16a34a", "#f59e0b", "#3b82f6"):
        assert literal not in html, f"{literal} must come from theme.ui()"


def test_marc_popup_discovery_chip_uses_info_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_dark(monkeypatch, False)
    popup = MarcEvidencePopup(
        needle="some discovery",
        exists_in=[],
        marc_record={"_control_number": "990002"},
        grounded=False,
    )
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415
    header = popup._build_header()
    html = " ".join(lbl.text() for lbl in header.findChildren(QLabel))
    assert theme.ui("info") in html
    assert "#3b82f6" not in html


def test_marc_popup_constructs_in_both_themes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for dark in (True, False):
        popup = _build_popup(monkeypatch, dark=dark)
        assert popup is not None


# ── match_comparison_dialog ────────────────────────────────────────────────


def _approved_row() -> dict:
    return {
        "source": "viaf",
        "entity_text": "Maimonides",
        "matched_name": "Maimonides",
        "matched_id": "12345",
        "approved": True,
    }


def _unmatched_row() -> dict:
    return {
        "source": "marc_field",
        "entity_text": "Some Name",
        "matched_name": "",
        "matched_id": "",
        "approved": False,
    }


def test_match_dialog_badge_uses_success_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_dark(monkeypatch, True)
    dialog = MatchComparisonDialog(_approved_row())
    html = dialog._header_label.text()
    expected = _rgb(theme.ui("success"))
    assert f"rgba({expected},180)" in html
    assert "rgba(34,197,94,180)" not in html


def test_match_dialog_banner_uses_warning_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_dark(monkeypatch, False)
    dialog = MatchComparisonDialog(_unmatched_row())
    html = dialog._header_label.text()
    assert theme.ui("warning") in html
    assert "rgba(245, 158, 11, 60)" not in html


def test_match_dialog_constructs_in_both_themes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for dark in (True, False):
        _force_dark(monkeypatch, dark)
        approved = MatchComparisonDialog(_approved_row())
        unmatched = MatchComparisonDialog(_unmatched_row())
        assert approved is not None
        assert unmatched is not None


def _rgb(value: str) -> str:
    s = value.lstrip("#")
    return f"{int(s[0:2], 16)}, {int(s[2:4], 16)}, {int(s[4:6], 16)}"
