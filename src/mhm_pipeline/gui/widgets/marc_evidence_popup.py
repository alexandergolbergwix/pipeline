"""Modal evidence popup for the "Exists in" column on the NER review table.

Renders the FULL MARC record (every text-bearing field) with the
matched entity text highlighted in green (full match) or yellow
(partial match), so a reviewer can confirm at a glance where the
predicted name / span actually appears in the source bibliographic
record.

Designed against Rule 37 — every QDialog must use the liquid-glass
backdrop — so this class subclasses :class:`GlassDialog` and uses the
companion stylesheet helpers.

Wired into :mod:`ExtractionEditor`: clicking a non-empty cell in the
"Exists in" column opens this popup with:

  * the entity's predicted text (the needle that was searched)
  * the entity's ``exists_in`` list (which fields matched and how)
  * the full MARC record from ``marc_extracted.json`` (so unmatched
    fields are also shown — context for the reviewer)
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.glass_dialog import GlassDialog


# ── Highlight colours ────────────────────────────────────────────────────
#
# Qt parses ``#RRGGBBAA`` ambiguously: an 8-hex string is ``#AARRGGBB``
# (alpha first), NOT the CSS convention. To avoid that footgun the
# helpers below return CSS strings for QSS contexts and QColor objects
# for QTextCharFormat — the latter constructed via fromRgb() with
# explicit channels so the alpha can never be re-interpreted as red.

# Base RGB tuples (alpha applied per-context below).
_FULL_RGB = (22, 163, 74)        # tailwind green-600
_PARTIAL_RGB = (245, 158, 11)    # tailwind amber-500


def _highlight_color(match_type: str) -> str:
    """Return a QSS-safe ``rgba(...)`` string for QSS contexts."""
    dark = theme.is_dark()
    if match_type == "full":
        r, g, b = _FULL_RGB
        return f"rgba({r},{g},{b},{0.33 if dark else 0.26})"
    if match_type == "partial":
        r, g, b = _PARTIAL_RGB
        return f"rgba({r},{g},{b},{0.33 if dark else 0.40})"
    return "transparent"


def _highlight_qcolor(match_type: str):
    """Return a ``QColor`` for QTextCharFormat backgrounds — channels
    set explicitly so Qt's hex parser can't reorder them."""
    from PyQt6.QtGui import QColor  # noqa: PLC0415
    dark = theme.is_dark()
    if match_type == "full":
        r, g, b = _FULL_RGB
        return QColor(r, g, b, 110 if dark else 80)
    if match_type == "partial":
        r, g, b = _PARTIAL_RGB
        return QColor(r, g, b, 120 if dark else 100)
    return QColor(0, 0, 0, 0)


# ── Field-name pretty labels ─────────────────────────────────────────────

_FIELD_LABELS: dict[str, str] = {
    "title": "245 — Title",
    "variant_titles": "246 — Variant titles",
    "authors": "100 / 110 — Authors",
    "contributors": "700 / 710 — Contributors",
    "provenance": "561 — Provenance",
    "notes": "500 — Notes",
    "contents": "505 — Contents",
    "colophon_text": "Colophon text",
    "data_from_colophon": "Colophon — extracted",
    "subjects": "650 — Subjects",
    "canonical_references": "Canonical references",
    "related_works": "Related works",
    "place": "Place",
    "related_places": "Related places",
    "dates": "260 / 264 — Dates",
    "shelfmark": "Shelfmark",
    "genres": "655 — Genres",
}


def _label_for(path: str) -> str:
    """Pretty-print a dotted MARC path for the popup header.

    ``"contributors[1].name"`` → ``"700 / 710 — Contributors [2]"``.
    """
    top = path.split(".", 1)[0].split("[", 1)[0]
    pretty = _FIELD_LABELS.get(top, top)
    # Preserve the indexer + sub-key suffix for traceability
    if "[" in path:
        idx = path.split("[", 1)[1].split("]", 1)[0]
        try:
            human_idx = int(idx) + 1
            pretty = f"{pretty}  [{human_idx}]"
        except ValueError:
            pass
    return pretty


# ── Match-index helpers ──────────────────────────────────────────────────


def _matches_by_field(exists_in: list[dict[str, Any]]) -> dict[str, str]:
    """Return ``{field_path: 'full'|'partial'}`` (full wins on collisions)."""
    out: dict[str, str] = {}
    for row in exists_in:
        field = str(row.get("field") or "")
        m = str(row.get("match_type") or "")
        if m not in {"full", "partial"}:
            continue
        if field not in out or m == "full":
            out[field] = m
    return out


# ── The popup ────────────────────────────────────────────────────────────


class MarcEvidencePopup(GlassDialog):
    """Show every MARC field with the matched text highlighted.

    Caller passes:

    * ``needle`` — the entity's predicted text (the user-visible
      "Entity" column value).
    * ``exists_in`` — the entity's ``exists_in`` list from
      ``ner_results.json``. Drives the highlight colours.
    * ``marc_record`` — the matching ``marc_extracted.json`` entry.
      Used to surface the FULL set of MARC fields, including those
      that did not match (gray / no highlight).
    """

    def __init__(
        self,
        *,
        needle: str,
        exists_in: list[dict[str, Any]],
        marc_record: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("MARC evidence — where this name appears")
        self.resize(820, 640)

        self._needle = (needle or "").strip()
        self._exists_in = exists_in or []
        self._marc_record = marc_record or {}

        outer = QVBoxLayout(self.glass_content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_legend())
        outer.addWidget(self._build_scroll_area(), stretch=1)
        outer.addLayout(self._build_buttons())

    # ── Header ───────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_XS)

        title = QLabel(f'Searching MARC for: "{self._needle}"')
        title.setStyleSheet(
            f"color: {theme.ui('text')}; "
            f"font-size: {theme.FONT_LG}px; font-weight: 600;"
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        # Summary line — how many full + partial hits
        n_full = sum(1 for r in self._exists_in
                     if r.get("match_type") == "full")
        n_partial = sum(1 for r in self._exists_in
                        if r.get("match_type") == "partial")
        cn = self._marc_record.get("_control_number") or "(no control number)"
        summary = QLabel(
            f"Record  {cn}  ·  "
            f"<span style=\"color:#16a34a;\">●</span> {n_full} full  ·  "
            f"<span style=\"color:#f59e0b;\">●</span> {n_partial} partial"
        )
        summary.setStyleSheet(
            f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;"
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(summary)

        return w

    # ── Legend ───────────────────────────────────────────────────────────

    def _build_legend(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_MD)

        def chip(label: str, bg: str) -> QLabel:
            c = QLabel(f"  {label}  ")
            c.setStyleSheet(
                f"background-color: {bg}; color: {theme.ui('text')}; "
                f"padding: 2px 8px; border-radius: {theme.RADIUS_SM}px; "
                f"font-size: {theme.FONT_XS}px;"
            )
            return c

        layout.addWidget(chip("Full match", _highlight_color("full")))
        layout.addWidget(chip("Partial match", _highlight_color("partial")))
        layout.addStretch(1)
        return w

    # ── Scroll area with every MARC field ────────────────────────────────

    def _build_scroll_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: 1px solid "
            f"{theme.ui('border')}; border-radius: {theme.RADIUS_MD}px; }}"
        )

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        body = QVBoxLayout(host)
        body.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        body.setSpacing(theme.SPACE_SM)

        match_map = _matches_by_field(self._exists_in)
        rendered_any = False
        for row_widget in self._iter_field_rows(match_map):
            body.addWidget(row_widget)
            rendered_any = True

        if not rendered_any:
            empty = QLabel("No text-bearing MARC fields in this record.")
            empty.setStyleSheet(
                f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px; "
                f"padding: {theme.SPACE_LG}px;"
            )
            body.addWidget(empty)

        body.addStretch(1)
        scroll.setWidget(host)
        return scroll

    def _iter_field_rows(
        self, match_map: dict[str, str],
    ) -> list[QWidget]:
        """Build one row per MARC field/sub-row in display order.

        Ordering: matched rows (full > partial) first, then the rest of
        the audited MARC fields. Within each group, fields appear in
        the canonical MARC order.
        """
        # Re-use the same field list the backend audits — keeps the
        # popup in sync with the matcher.
        from converter.authority.ner_post_filters import _iter_audit_fields  # noqa: PLC0415
        audit_rows = _iter_audit_fields(self._marc_record)

        # Stable sort: rows whose dotted-path is in match_map come first,
        # full before partial, then everything else.
        def sort_key(row: tuple[str, str]) -> tuple[int, int]:
            path = row[0]
            if path in match_map:
                return (0, 0 if match_map[path] == "full" else 1)
            return (1, 0)

        sorted_rows = sorted(audit_rows, key=sort_key)
        out: list[QWidget] = []
        for path, value in sorted_rows:
            match_type = match_map.get(path, "")
            out.append(self._build_field_row(path, value, match_type))
        return out

    def _build_field_row(
        self, path: str, value: str, match_type: str,
    ) -> QWidget:
        """One row: pretty label + value text edit with highlighted
        matches (if any)."""
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Left chip indicating match strength
        label = QLabel(_label_for(path))
        label_style = (
            f"color: {theme.ui('text')}; font-size: {theme.FONT_SM}px; "
            f"font-weight: 600;"
        )
        if match_type:
            label_style += (
                f" background-color: {_highlight_color(match_type)}; "
                f"padding: 1px 6px; border-radius: {theme.RADIUS_SM}px;"
            )
        label.setStyleSheet(label_style)
        layout.addWidget(label)

        # The value cell — QTextEdit gives us per-character highlights
        # via QTextCharFormat. Read-only, transparent background, RTL-aware.
        value_widget = QTextEdit()
        value_widget.setReadOnly(True)
        value_widget.setPlainText(value)
        value_widget.setStyleSheet(
            f"QTextEdit {{ background: transparent; color: {theme.ui('text')}; "
            f"border: 1px solid {theme.ui('border')}; "
            f"border-radius: {theme.RADIUS_SM}px; "
            f"padding: {theme.SPACE_XS}px; "
            f"font-size: {theme.FONT_SM}px; }}"
        )
        font = QFont()
        font.setPointSize(max(10, theme.FONT_SM))
        value_widget.setFont(font)
        # Size to roughly 2 lines for short fields, more for long ones.
        rough_lines = max(2, min(8, value.count("\n") + len(value) // 90 + 1))
        value_widget.setFixedHeight(rough_lines * 22)

        if match_type and self._needle:
            self._apply_highlight(value_widget, self._needle, match_type)

        layout.addWidget(value_widget)
        return host

    def _apply_highlight(
        self, widget: QTextEdit, needle: str, match_type: str,
    ) -> None:
        """Highlight every case-insensitive occurrence of ``needle``
        (or any of its tokens, for partial-match rows) in the widget."""
        fmt = QTextCharFormat()
        fmt.setBackground(_highlight_qcolor(match_type))

        full_text = widget.toPlainText()
        cursor = widget.textCursor()

        if match_type == "full":
            # Try whole-needle first; if it doesn't appear (e.g. the
            # match came from a token-set-equality on a swapped order),
            # fall back to tokenwise.
            if _hit_substring(full_text, needle):
                self._highlight_substrings(cursor, full_text, [needle], fmt)
                return
        # Tokenwise highlight (partial mode, or full-but-swap fallback)
        tokens = [
            t for t in _split_for_match(needle) if t
        ]
        if tokens:
            self._highlight_substrings(cursor, full_text, tokens, fmt)

    @staticmethod
    def _highlight_substrings(
        cursor: QTextCursor, text: str, needles: list[str],
        fmt: QTextCharFormat,
    ) -> None:
        """Apply ``fmt`` to every (case-insensitive) occurrence of each
        needle in ``text``."""
        lo = text.lower()
        for n in needles:
            if not n:
                continue
            n_lo = n.lower()
            pos = 0
            while True:
                idx = lo.find(n_lo, pos)
                if idx < 0:
                    break
                cursor.setPosition(idx)
                cursor.setPosition(idx + len(n), QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(fmt)
                pos = idx + len(n)

    # ── Footer ───────────────────────────────────────────────────────────

    def _build_buttons(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(theme.button_style())
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)
        return layout


# ── Local helpers ────────────────────────────────────────────────────────


def _qcolor(rgba: str):
    """Return a QColor for a CSS-ish ``#rrggbbaa`` string."""
    from PyQt6.QtGui import QColor  # noqa: PLC0415
    return QColor(rgba)


def _hit_substring(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


def _split_for_match(text: str) -> list[str]:
    """Split on whitespace + common punctuation for tokenwise highlight."""
    import re  # noqa: PLC0415
    return [t for t in re.split(r"[\s,;:.\"'\[\]()<>״׳]+", text) if t]


__all__ = ["MarcEvidencePopup"]
