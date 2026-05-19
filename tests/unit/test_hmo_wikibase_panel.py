"""Focused tests for the optional HMO Wikibase export panel."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.panels.hmo_wikibase_panel import HmoWikibasePanel  # noqa: E402


@pytest.fixture()
def panel(qtbot: object) -> HmoWikibasePanel:
    """Create the panel on the offscreen Qt platform."""
    if QApplication.instance() is None:
        QApplication([])
    widget = HmoWikibasePanel()
    qtbot.addWidget(widget)  # type: ignore[attr-defined]
    widget.show()
    return widget


def test_panel_constructs_with_default_wikibase_url(panel: HmoWikibasePanel) -> None:
    assert panel._wikibase_url_edit.text() == "https://mhm-hmo.wikibase.cloud"
    assert panel.current_ttl_path() is None
    assert panel._table.columnCount() == 5


def test_main_window_integration_accessors(
    panel: HmoWikibasePanel,
    tmp_path: Path,
) -> None:
    ttl_path = tmp_path / "output.ttl"
    output_dir = tmp_path / "wikibase"

    panel.set_ttl_path(ttl_path)
    panel.set_output_dir(output_dir)

    assert panel.current_ttl_path() == ttl_path
    assert panel._output_selector.path == output_dir


def test_build_draft_writes_expected_artifacts(
    panel: HmoWikibasePanel,
    qtbot: object,
    tmp_path: Path,
) -> None:
    ttl_path = tmp_path / "output.ttl"
    ttl_path.write_text(
        "\n".join(
            [
                "@prefix ex: <https://example.org/> .",
                "@prefix hm: <http://example.org/hm#> .",
                "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
                "",
                'ex:MS1 a hm:DigitalAccess ;',
                '  rdfs:label "Manuscript One"@en ;',
                '  hm:sourceShelfmark "Shelfmark 1" .',
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "exports"
    panel.set_ttl_path(ttl_path)
    panel.set_output_dir(output_dir)

    with qtbot.waitSignal(panel._build_btn.clicked, timeout=1000):  # type: ignore[attr-defined]
        panel._build_btn.click()
    qtbot.waitUntil(lambda: panel._worker is None, timeout=5000)  # type: ignore[attr-defined]

    json_path = output_dir / "wikibase_entities.json"
    quickstatements_path = output_dir / "wikibase_quickstatements.txt"
    report_path = output_dir / "wikibase_export_report.json"

    assert json_path.exists()
    assert quickstatements_path.exists()
    assert report_path.exists()
    assert panel._entities_value.text() == "1"
    assert panel._statements_value.text() == "1"
    assert panel._table.rowCount() == 1

    entities = json.loads(json_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert entities[0]["local_id"] == "QDraft_MS1"
    assert report["upload_performed"] is False
    assert report["total_entities"] == 1
    assert report["total_statements"] == 1
    assert "CREATE\tQDraft_MS1" in quickstatements_path.read_text(encoding="utf-8")
