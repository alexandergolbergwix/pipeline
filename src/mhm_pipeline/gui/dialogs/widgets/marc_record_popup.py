"""Popup showing all original MARC data for one record.

Wired into every results/edit table: clicking a record-id (001 / control
number) cell opens this popup with the full original MARC record rendered
as a friendly, searchable tree.

Reuses :class:`JsonTreeViewer` (theme-tokened, Rule 36) inside a
:class:`GlassDialog` (Rule 37). A module-level ``load_marc_index`` caches
``marc_extracted.json`` per output dir so repeated clicks don't re-read disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QLabel, QLineEdit, QVBoxLayout

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.dialogs.widgets.json_tree_viewer import JsonTreeViewer
from mhm_pipeline.gui.widgets.glass_dialog import GlassDialog

# Friendly labels for the MARC semantic keys (raw key shown in the node
# tooltip by JsonTreeViewer). Unmapped keys render with their raw name.
_FRIENDLY_KEYS: dict[str, str] = {
    "_control_number": "Manuscript ID",
    "001": "Manuscript ID",
    "title": "Title",
    "title_variants": "Title variants",
    "uniform_title": "Uniform title",
    "alternate_titles": "Alternate titles",
    "authors": "Authors",
    "contributors": "Contributors",
    "subjects": "Subjects",
    "genres": "Genres",
    "genre_form": "Genre / form",
    "dates": "Dates",
    "shelfmark": "Shelfmark",
    "provenance": "Provenance",
    "former_owners": "Former owners",
    "ownership_history": "Ownership history",
    "acquisition_source": "Acquisition source",
    "notes": "Notes",
    "contents": "Contents",
    "works": "Works",
    "places": "Places",
    "related_places": "Related places",
    "colophon_text": "Colophon",
    "data_from_colophon": "Data from colophon",
    "holding_institution": "Holding institution",
    "digital_url": "Digital URL",
    "extent": "Extent",
    "height_mm": "Height (mm)",
    "external_ids": "External IDs",
}

# Per-output-dir cache: {output_dir_str: {control_number: marc_record}}.
_INDEX_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


def load_marc_index(output_dir: Path | str) -> dict[str, dict[str, Any]]:
    """Load + cache ``marc_extracted.json`` from *output_dir*, indexed by id.

    Returns an empty dict when the file is missing or unparseable — callers
    treat a missing record gracefully (the popup shows whatever it was given).
    """
    key = str(output_dir)
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    index: dict[str, dict[str, Any]] = {}
    path = Path(output_dir) / "marc_extracted.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = []
        records = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            cn = str(rec.get("_control_number") or rec.get("001") or "").strip()
            if cn:
                index[cn] = rec
    _INDEX_CACHE[key] = index
    return index


def clear_marc_index_cache() -> None:
    """Drop the cached indexes (call after a new pipeline run writes output)."""
    _INDEX_CACHE.clear()


class MarcRecordPopup(GlassDialog):
    """Friendly tree of one record's full original MARC data."""

    def __init__(
        self,
        control_number: str,
        marc_record: dict[str, Any] | None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"MARC record · {control_number}")
        self.resize(640, 560)

        layout = QVBoxLayout(self.glass_content)
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_SM)

        heading = QLabel(f"Original MARC record — {control_number}")
        heading.setStyleSheet(
            f"color:{theme.ui('text')}; font-size:{theme.FONT_LG}px;"
            f" font-weight:{theme.WEIGHT_SEMIBOLD}; background: transparent;"
        )
        layout.addWidget(heading)

        if not marc_record:
            empty = QLabel(
                "No original MARC data is available for this record in the "
                "current output directory."
            )
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;"
                f" background: transparent;"
            )
            layout.addWidget(empty)
            return

        search = QLineEdit()
        search.setPlaceholderText("Search fields…")
        search.setStyleSheet(
            f"QLineEdit {{ color:{theme.ui('text')};"
            f" background: {theme.ui('panel_bg')};"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_SM}px; padding: 6px 8px;"
            f" font-size: {theme.FONT_SM}px; }}"
        )
        layout.addWidget(search)

        self._tree = JsonTreeViewer(marc_record, friendly_key_map=_FRIENDLY_KEYS)
        layout.addWidget(self._tree, 1)
        search.textChanged.connect(self._tree.search)


def open_marc_popup(
    control_number: str,
    marc_record: dict[str, Any] | None,
    parent: Any = None,
) -> MarcRecordPopup:
    """Construct + show a non-modal MARC popup; return it (for tests/refs)."""
    popup = MarcRecordPopup(control_number, marc_record, parent=parent)
    popup.show()
    return popup


__all__ = [
    "MarcRecordPopup",
    "open_marc_popup",
    "load_marc_index",
    "clear_marc_index_cache",
]
