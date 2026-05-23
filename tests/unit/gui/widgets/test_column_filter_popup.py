"""Tests for the per-column value-filter popup + filter-proxy integration.

Covers Rule 49 §E:
* :class:`ColumnFilterPopup` rendering, search, select-all / clear-all,
  Apply / Cancel behaviour.
* :func:`distinct_values_from_rows` / :func:`counts_from_rows` helpers.
* Per-column filter integration with :class:`AuthorityFilterProxy` and
  :class:`EntityFilterProxy` — proves the new ``set_column_filter`` /
  ``clear_all_column_filters`` API filters rows correctly and ANDs
  with the existing chip-row dimension filter.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mhm_pipeline.gui.widgets.authority_editor import (  # noqa: E402
    COL_CONF,
    COL_SOURCE,
    COL_TYPE,
    AuthorityFilterProxy,
    AuthorityMatchModel,
    cell_value_for_filter,
    flatten_authority_records,
)
from mhm_pipeline.gui.widgets.column_filter_popup import (  # noqa: E402
    BLANK_LABEL,
    ColumnFilterPopup,
    counts_from_rows,
    distinct_values_from_rows,
)
from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: E402
    COL_ROLE,
    EditableEntityModel,
    EntityFilterProxy,
    entity_cell_value_for_filter,
)
from mhm_pipeline.gui.widgets.extraction_editor import (  # noqa: E402
    COL_TYPE as NER_COL_TYPE,
)


@pytest.fixture(autouse=True)
def _qapp_offscreen() -> None:
    """Each test gets a live ``QApplication`` (popup theme reads it)."""
    app = QApplication.instance() or QApplication([])
    yield
    del app


# ─────────────────────────────────────────────────────────────────────
# distinct_values_from_rows + counts_from_rows
# ─────────────────────────────────────────────────────────────────────


class TestDistinctValuesAndCounts:
    def test_distinct_values_sorted_with_blank_first(self) -> None:
        rows = [{"x": "b"}, {"x": "a"}, {"x": ""}, {"x": "a"}]
        out = distinct_values_from_rows(rows, lambda r: r["x"])
        # Empty-string bucket sorts BEFORE the lettered ones (empty
        # first), then "a" < "b".
        assert out == ["", "a", "b"]

    def test_counts_per_value(self) -> None:
        rows = [{"x": "a"}, {"x": "a"}, {"x": "b"}, {"x": ""}]
        counts = counts_from_rows(rows, lambda r: r["x"])
        assert counts == {"a": 2, "b": 1, "": 1}


# ─────────────────────────────────────────────────────────────────────
# ColumnFilterPopup
# ─────────────────────────────────────────────────────────────────────


class TestColumnFilterPopupConstruction:
    def test_renders_with_three_values(self) -> None:
        popup = ColumnFilterPopup("Type", ["person", "place", "work"])
        assert popup._list.count() == 3
        labels = {
            popup._list.item(i).text()  # type: ignore[union-attr]
            for i in range(popup._list.count())
        }
        assert labels == {"person", "place", "work"}

    def test_renders_counts_when_provided(self) -> None:
        popup = ColumnFilterPopup(
            "Type", ["person", "place"], counts={"person": 5, "place": 2},
        )
        labels = [popup._list.item(i).text() for i in range(popup._list.count())]
        assert any("(5)" in label for label in labels)
        assert any("(2)" in label for label in labels)

    def test_renders_blank_sentinel_for_empty_string(self) -> None:
        popup = ColumnFilterPopup("Type", ["", "person"])
        labels = [popup._list.item(i).text() for i in range(popup._list.count())]
        assert BLANK_LABEL in labels

    def test_initial_all_checked_when_no_filter(self) -> None:
        popup = ColumnFilterPopup("Type", ["a", "b", "c"])
        states = [
            popup._list.item(i).checkState()  # type: ignore[union-attr]
            for i in range(popup._list.count())
        ]
        assert all(s == Qt.CheckState.Checked for s in states)

    def test_initial_only_selected_checked_when_filter_active(self) -> None:
        popup = ColumnFilterPopup("Type", ["a", "b", "c"], selected={"b"})
        states = {
            popup._list.item(i).data(Qt.ItemDataRole.UserRole): popup._list.item(i).checkState()  # type: ignore[union-attr]
            for i in range(popup._list.count())
        }
        assert states["b"] == Qt.CheckState.Checked
        assert states["a"] == Qt.CheckState.Unchecked
        assert states["c"] == Qt.CheckState.Unchecked


class TestColumnFilterPopupSearch:
    def test_typing_filters_visible_items(self) -> None:
        popup = ColumnFilterPopup("Type", ["person", "place", "work"])
        popup._search.setText("pl")
        hidden = [popup._list.item(i).isHidden() for i in range(popup._list.count())]
        # Only "place" matches "pl".
        labels_visible = [
            popup._list.item(i).text()  # type: ignore[union-attr]
            for i in range(popup._list.count())
            if not hidden[i]
        ]
        assert labels_visible == ["place"]

    def test_select_all_only_affects_visible(self) -> None:
        popup = ColumnFilterPopup("Type", ["person", "place", "work"])
        # Start with everyone unchecked, then filter, then select-all.
        popup._on_clear_all()
        popup._search.setText("pl")
        popup._on_select_all()
        states = {
            popup._list.item(i).data(Qt.ItemDataRole.UserRole): popup._list.item(i).checkState()  # type: ignore[union-attr]
            for i in range(popup._list.count())
        }
        assert states["place"] == Qt.CheckState.Checked
        # Hidden items stay unchecked.
        assert states["person"] == Qt.CheckState.Unchecked
        assert states["work"] == Qt.CheckState.Unchecked

    def test_clear_all_only_affects_visible(self) -> None:
        popup = ColumnFilterPopup("Type", ["person", "place", "work"])
        popup._search.setText("pl")
        popup._on_clear_all()
        states = {
            popup._list.item(i).data(Qt.ItemDataRole.UserRole): popup._list.item(i).checkState()  # type: ignore[union-attr]
            for i in range(popup._list.count())
        }
        # Hidden items keep their initial (checked) state.
        assert states["place"] == Qt.CheckState.Unchecked
        assert states["person"] == Qt.CheckState.Checked
        assert states["work"] == Qt.CheckState.Checked


class TestColumnFilterPopupApply:
    def test_apply_emits_checked_subset_when_partial(self) -> None:
        popup = ColumnFilterPopup("Type", ["person", "place", "work"])
        # Uncheck "work"
        for i in range(popup._list.count()):
            item = popup._list.item(i)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == "work":
                item.setCheckState(Qt.CheckState.Unchecked)

        captured: list[set[str]] = []
        popup.selection_changed.connect(lambda s: captured.append(s))
        popup._on_apply()
        assert captured == [{"person", "place"}]

    def test_apply_emits_empty_set_when_all_checked_means_no_filter(self) -> None:
        popup = ColumnFilterPopup("Type", ["a", "b", "c"])
        captured: list[set[str]] = []
        popup.selection_changed.connect(lambda s: captured.append(s))
        popup._on_apply()
        # Every item checked → "no filter" → empty set.
        assert captured == [set()]


# ─────────────────────────────────────────────────────────────────────
# AuthorityFilterProxy per-column filter integration
# ─────────────────────────────────────────────────────────────────────


def _three_authority_records() -> list[dict]:
    return [
        {
            "_control_number": "001",
            "dates": {"year": 1650},
            "marc_authority_matches": [
                {
                    "name": "משה בן מימון",
                    "role": "author",
                    "field": "100",
                    "confidence": "high",
                    "mazal_id": "987",
                    "viaf_uri": "https://viaf.org/viaf/123",
                    "preferred_name_lat": "Maimonides",
                    "birth_year": 1138,
                    "death_year": 1204,
                },
            ],
        },
        {
            "_control_number": "002",
            "dates": {"year": 1700},
            "marc_authority_matches": [
                {
                    "name": "אברהם",
                    "role": "scribe",
                    "field": "700",
                    "confidence": "medium",
                },
            ],
        },
        {
            "_control_number": "003",
            "dates": {"year": 1800},
            "kima_places": {"ירושלים": "https://www.wikidata.org/entity/Q1218"},
        },
    ]


class TestAuthorityFilterProxyPerColumn:
    def _setup(self) -> tuple[AuthorityMatchModel, AuthorityFilterProxy]:
        model = AuthorityMatchModel()
        model.load(_three_authority_records())
        proxy = AuthorityFilterProxy()
        proxy.setSourceModel(model)
        return model, proxy

    def test_no_column_filter_leaves_all_rows_visible(self) -> None:
        _model, proxy = self._setup()
        assert proxy.rowCount() == 3

    def test_set_column_filter_on_type_restricts_to_chosen_values(self) -> None:
        model, proxy = self._setup()
        # Rows have match_type ∈ {"person", "place"} after KIMA row.
        proxy.set_column_filter(COL_TYPE, {"person"})
        # 2 person rows from records 001 + 002 — but only those whose
        # display value is "person". Verify by scanning.
        proxy_rows = [
            cell_value_for_filter(model, model._rows.index(model._rows[i]), COL_TYPE)
            for i in range(model.rowCount())
        ]
        person_count = sum(1 for v in proxy_rows if v == "person")
        assert proxy.rowCount() == person_count

    def test_set_column_filter_to_empty_set_clears_that_column(self) -> None:
        _model, proxy = self._setup()
        proxy.set_column_filter(COL_TYPE, {"person"})
        assert proxy.has_any_column_filter() is True
        proxy.set_column_filter(COL_TYPE, set())
        assert proxy.has_any_column_filter() is False
        assert proxy.rowCount() == 3

    def test_filter_ands_with_dimension_chip_filter(self) -> None:
        _model, proxy = self._setup()
        # Chip-row: only "MARC 100"
        proxy.set_dimension_filters(
            sources={"MARC 100 (Main entry — person)"}, types=set(), bands=set(),
        )
        # Column filter: only confidence band "low" → no row qualifies.
        proxy.set_column_filter(COL_CONF, {"low"})
        assert proxy.rowCount() == 0

    def test_clear_all_column_filters_removes_every_active_filter(self) -> None:
        _model, proxy = self._setup()
        proxy.set_column_filter(COL_TYPE, {"person"})
        proxy.set_column_filter(COL_SOURCE, {"foo"})
        assert proxy.has_any_column_filter() is True
        proxy.clear_all_column_filters()
        assert proxy.has_any_column_filter() is False
        assert proxy.rowCount() == 3

    def test_filtered_header_decorated_with_glyph(self) -> None:
        _model, proxy = self._setup()
        proxy.set_column_filter(COL_TYPE, {"person"})
        decorated = proxy.headerData(
            COL_TYPE, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole,
        )
        assert isinstance(decorated, str)
        assert decorated.endswith("▾")
        # Unfiltered columns are not decorated.
        plain = proxy.headerData(
            COL_SOURCE, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole,
        )
        assert isinstance(plain, str)
        assert "▾" not in plain


class TestAuthorityMsYearPlumbing:
    def test_ms_year_copied_onto_marc_match_rows(self) -> None:
        records = _three_authority_records()
        rows = flatten_authority_records(records)
        marc_rows = [r for r in rows if r["_origin_kind"] == "marc"]
        assert len(marc_rows) == 2
        assert marc_rows[0]["_ms_year"] == 1650
        assert marc_rows[1]["_ms_year"] == 1700

    def test_ms_year_copied_onto_kima_rows(self) -> None:
        records = _three_authority_records()
        rows = flatten_authority_records(records)
        kima_rows = [r for r in rows if r["_origin_kind"] == "kima"]
        assert len(kima_rows) == 1
        assert kima_rows[0]["_ms_year"] == 1800
        # KIMA rows do NOT carry candidate birth/death.
        assert kima_rows[0]["_birth_year"] is None
        assert kima_rows[0]["_death_year"] is None


# ─────────────────────────────────────────────────────────────────────
# Dates-column rendering
# ─────────────────────────────────────────────────────────────────────


class TestDatesColumnRendering:
    def test_dual_format_with_both_sides_and_no_conflict(self) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import _format_dates_cell
        row = {
            "_auth_kind": "mazal",
            "_ms_year": 1650,
            "_birth_year": 1138,
            "_death_year": 1204,
            "_guard_flags": ["has_wikidata"],
        }
        assert _format_dates_cell(row) == "MS 1650 | 1138–1204 ✓"

    def test_conflict_glyph_when_date_conflict_in_guard_flags(self) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import _format_dates_cell
        row = {
            "_auth_kind": "mazal",
            "_ms_year": 1500,
            "_birth_year": 1700,
            "_death_year": 1770,
            "_guard_flags": ["date_conflict"],
        }
        out = _format_dates_cell(row)
        assert "✗" in out
        assert "MS 1500" in out and "1700–1770" in out

    def test_partial_glyph_when_only_one_candidate_year(self) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import _format_dates_cell
        row = {
            "_auth_kind": "viaf",
            "_ms_year": 1650,
            "_birth_year": 1138,
            "_death_year": None,
            "_guard_flags": [],
        }
        out = _format_dates_cell(row)
        assert "⚠" in out
        assert "1138–?" in out

    def test_kima_row_renders_em_dash(self) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import _format_dates_cell
        row = {
            "_auth_kind": "kima",
            "_ms_year": 1800,
        }
        assert _format_dates_cell(row) == "—"

    def test_no_dates_renders_em_dash(self) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import _format_dates_cell
        row = {
            "_auth_kind": "mazal",
            "_ms_year": None,
            "_birth_year": None,
            "_death_year": None,
            "_guard_flags": [],
        }
        assert _format_dates_cell(row) == "—"

    def test_tooltip_includes_role_specific_rule(self) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import _build_authority_dates_tooltip
        row = {
            "_auth_kind": "mazal",
            "role": "author",
            "_ms_year": 1650,
            "_birth_year": 1138,
            "_death_year": 1204,
            "_guard_flags": ["has_wikidata"],
        }
        html = _build_authority_dates_tooltip(row)
        assert "Textual-authorship role" in html
        assert "1138" in html and "1204" in html and "1650" in html

    def test_tooltip_subject_role_explains_about_ness(self) -> None:
        from mhm_pipeline.gui.widgets.authority_editor import _build_authority_dates_tooltip
        row = {
            "_auth_kind": "mazal",
            "role": "subject",
            "_ms_year": 1750,
            "_birth_year": 1138,
            "_death_year": 1204,
            "_guard_flags": [],
        }
        html = _build_authority_dates_tooltip(row)
        assert "Subject role" in html
        assert "about someone" in html.lower()


# ─────────────────────────────────────────────────────────────────────
# EntityFilterProxy per-column filter integration (NER side)
# ─────────────────────────────────────────────────────────────────────


class TestEntityFilterProxyPerColumn:
    def _setup(self) -> tuple[EditableEntityModel, EntityFilterProxy]:
        model = EditableEntityModel()
        model.load_from_records([
            {
                "_control_number": "001",
                "entities": [
                    {
                        "text": "Maimonides",
                        "type": "PERSON",
                        "role": "AUTHOR",
                        "source": "person_ner",
                        "confidence": 0.85,
                        "model_confidence": 0.91,
                    },
                    {
                        "text": "Rashi",
                        "type": "PERSON",
                        "role": "AUTHOR",
                        "source": "person_ner",
                        "confidence": 0.85,
                        "model_confidence": 0.87,
                    },
                    {
                        "text": "Padova",
                        "type": "DATE",
                        "role": "",
                        "source": "provenance_ner",
                        "confidence": 0.60,
                        "model_confidence": 0.74,
                    },
                ],
            },
        ])
        proxy = EntityFilterProxy()
        proxy.setSourceModel(model)
        return model, proxy

    def test_role_column_filter_restricts_to_authors_only(self) -> None:
        _model, proxy = self._setup()
        proxy.set_column_filter(COL_ROLE, {"AUTHOR"})
        assert proxy.rowCount() == 2

    def test_type_column_filter_restricts_to_persons(self) -> None:
        _model, proxy = self._setup()
        proxy.set_column_filter(NER_COL_TYPE, {"PERSON"})
        assert proxy.rowCount() == 2

    def test_multi_column_filter_ands_together(self) -> None:
        _model, proxy = self._setup()
        proxy.set_column_filter(NER_COL_TYPE, {"PERSON"})
        proxy.set_column_filter(COL_ROLE, {"AUTHOR"})
        assert proxy.rowCount() == 2  # both PERSON+AUTHOR rows pass

        # Now drop "AUTHOR" from the role allowlist → no rows qualify.
        proxy.set_column_filter(COL_ROLE, {"NONEXISTENT"})
        assert proxy.rowCount() == 0

    def test_entity_cell_value_for_filter_matches_displayrole(self) -> None:
        model, _proxy = self._setup()
        # Same string the popup will put in the checkbox → cell_value
        # accessor must return the same thing the data() method emits.
        for i in range(model.rowCount()):
            from PyQt6.QtCore import QModelIndex
            display = model.data(model.index(i, COL_ROLE), Qt.ItemDataRole.DisplayRole)
            filter_value = entity_cell_value_for_filter(model, i, COL_ROLE)
            assert str(display or "") == filter_value
            del QModelIndex  # silence flake8 lint about unused import
