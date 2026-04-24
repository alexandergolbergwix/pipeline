"""Side-by-side biodata comparison dialog for authority matches.

Opens when the reviewer clicks the 🧬 icon on an authority-match row.
Displays every biographical field (dates / places / names / occupations
/ raw) from the MARC record alongside the same field from the matched
authority (Mazal / VIAF / KIMA), coloured by diff status so the
reviewer can approve / reject with a single glance.

Fetching is lazy + threaded via :class:`_BioDataRunnable`. Results are
cached per ``(source, authority_id)`` for the life of the process.
"""

from __future__ import annotations

import logging
import sys
import unicodedata
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QMutex, QMutexLocker, QObject, QRunnable, Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_repo = Path(__file__).resolve().parents[4]
for _p in (str(_repo), str(_repo / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from converter.authority.biodata import (  # noqa: E402
    BioComparison,
    BioData,
    extract_kima_biodata,
    extract_marc_biodata,
    extract_mazal_biodata,
    extract_viaf_biodata,
)
from mhm_pipeline.gui.widgets.glass_dialog import (  # noqa: E402
    GlassDialog,
    glass_table_style,
    glass_tab_style,
)

logger = logging.getLogger(__name__)


# ── Session-scoped cache (thread-safe) ─────────────────────────────────
_CACHE: dict[tuple[str, str], BioComparison] = {}
_CACHE_MUTEX = QMutex()


def _cache_get(source: str, auth_id: str) -> BioComparison | None:
    with QMutexLocker(_CACHE_MUTEX):
        return _CACHE.get((source, auth_id))


def _cache_put(source: str, auth_id: str, cmp_: BioComparison) -> None:
    with QMutexLocker(_CACHE_MUTEX):
        _CACHE[(source, auth_id)] = cmp_


# ── Signals holder (QRunnable can't carry its own signals) ──────────────
class _BioDataSignals(QObject):
    ready = pyqtSignal(str, str, object)    # (source, auth_id, BioComparison)
    failed = pyqtSignal(str, str, str)      # (source, auth_id, message)


class _BioDataRunnable(QRunnable):
    """Fetch authority data + build a :class:`BioComparison` off-thread.

    The fetcher functions are injected so the dialog can be tested with
    fakes: ``viaf_fetcher(id) -> raw_dict``, ``mazal_fetcher(id) -> row``,
    ``kima_fetcher(id) -> row``. Each may be ``None`` if the matcher is
    unavailable (ungraceful, degrade to blank authority side).
    """

    def __init__(
        self,
        *,
        source: str,
        auth_id: str,
        marc_record: dict | None,
        viaf_fetcher: Any = None,
        mazal_fetcher: Any = None,
        kima_fetcher: Any = None,
    ) -> None:
        super().__init__()
        self.signals = _BioDataSignals()
        self._source = source
        self._auth_id = auth_id
        self._marc_record = marc_record
        self._viaf_fetcher = viaf_fetcher
        self._mazal_fetcher = mazal_fetcher
        self._kima_fetcher = kima_fetcher

    def run(self) -> None:    # noqa: D401
        cached = _cache_get(self._source, self._auth_id)
        if cached is not None:
            self.signals.ready.emit(self._source, self._auth_id, cached)
            return

        try:
            marc_bio = extract_marc_biodata(self._marc_record)
            if self._source == "viaf" and self._viaf_fetcher is not None:
                raw = self._viaf_fetcher(self._auth_id) or {}
                auth_bio = extract_viaf_biodata(raw)
            elif self._source == "mazal" and self._mazal_fetcher is not None:
                raw = self._mazal_fetcher(self._auth_id) or {}
                auth_bio = extract_mazal_biodata(raw)
            elif self._source == "kima" and self._kima_fetcher is not None:
                raw = self._kima_fetcher(self._auth_id) or {}
                auth_bio = extract_kima_biodata(raw)
            else:
                auth_bio = BioData()
            cmp_ = BioComparison(
                marc=marc_bio,
                authority=auth_bio,
                source=self._source,
            )
            _cache_put(self._source, self._auth_id, cmp_)
            self.signals.ready.emit(self._source, self._auth_id, cmp_)
        except Exception as exc:   # noqa: BLE001
            logger.warning("Biodata fetch failed (%s/%s): %s",
                           self._source, self._auth_id, exc)
            self.signals.failed.emit(self._source, self._auth_id, str(exc))


def fetch_biodata_async(
    *,
    source: str,
    auth_id: str,
    marc_record: dict | None,
    viaf_fetcher: Any = None,
    mazal_fetcher: Any = None,
    kima_fetcher: Any = None,
) -> _BioDataSignals:
    """Kick off a background biodata fetch. Returns the signals holder
    so the caller can hook ``ready`` / ``failed``."""
    runnable = _BioDataRunnable(
        source=source, auth_id=auth_id, marc_record=marc_record,
        viaf_fetcher=viaf_fetcher, mazal_fetcher=mazal_fetcher,
        kima_fetcher=kima_fetcher,
    )
    QThreadPool.globalInstance().start(runnable)
    return runnable.signals


# ── Diff helpers ───────────────────────────────────────────────────────


def _norm(text: str) -> str:
    """Unicode NFKC + case-fold + strip trailing punct for equality."""
    n = unicodedata.normalize("NFKC", str(text or ""))
    n = n.strip().casefold()
    return n.rstrip(",;:. ")


def _diff_pairs(marc_vals: list[str], auth_vals: list[str]) -> list[tuple[str, str, str]]:
    """Return ``(marc, auth, verdict)`` rows where *verdict* is one of
    ``matched`` / ``differs`` / ``only-in-marc`` / ``only-in-authority``.

    A MARC value is ``matched`` if any authority value normalises equal;
    the pairing is many-to-many, so we emit one row per unique value.
    """
    marc_set = {_norm(v): v for v in marc_vals if v}
    auth_set = {_norm(v): v for v in auth_vals if v}
    out: list[tuple[str, str, str]] = []
    for k, v in marc_set.items():
        if k in auth_set:
            out.append((v, auth_set[k], "matched"))
        else:
            out.append((v, "", "only-in-marc"))
    for k, v in auth_set.items():
        if k not in marc_set:
            out.append(("", v, "only-in-authority"))
    return out


# ── Dialog ─────────────────────────────────────────────────────────────


class MatchComparisonDialog(GlassDialog):
    """Glass dialog showing side-by-side MARC↔authority biodata.

    Footer exposes **✓ Approve**, **→ Next**, and **Close** so the
    reviewer can bulk-approve matches without dismissing the dialog
    between rows. The Approve + Next buttons call back into a
    :class:`NavigationController` that the parent editor supplies;
    that keeps this widget layer-free of model knowledge.
    """

    # Callback protocol for the parent editor. Each attribute is a
    # callable the dialog invokes on user action. Keeping the dialog
    # ignorant of the parent model keeps its tests trivial.
    #   controller.approve(row_dict) -> None
    #   controller.next(current_row_dict) -> NextState | None  where
    #     NextState has .row (new dict) and .comparison_getter (callable
    #     returning a BioComparison or kicking off an async fetch).

    def __init__(
        self,
        row: dict,
        parent: QWidget | None = None,
        *,
        comparison: BioComparison | None = None,
        on_approve: Any = None,
        on_next: Any = None,
    ) -> None:
        super().__init__(parent)
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        self._row = row
        self._theme = theme
        self._on_approve = on_approve
        self._on_next = on_next

        self.setWindowTitle("Compare authority match")
        self.resize(960, 680)
        self.setMinimumSize(720, 500)

        outer = QVBoxLayout(self.glass_content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        # Header (will be rebuilt on navigation so extracted into a setter)
        self._header_label = QLabel()
        self._header_label.setTextFormat(Qt.TextFormat.RichText)
        self._header_label.setWordWrap(True)
        outer.addWidget(self._header_label)
        self._refresh_header()

        # Progress bar shown while a fetch is in flight
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        outer.addWidget(self._progress)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(glass_tab_style(theme))
        outer.addWidget(self._tabs, stretch=1)

        self._dates_tab = self._make_diff_tab(
            headers=("Field", "MARC record", "Authority", "Status"),
        )
        self._places_tab = self._make_diff_tab(
            headers=("Type", "MARC record", "Authority", "Status"),
        )
        self._names_tab = self._make_diff_tab(
            headers=("Language", "MARC record", "Authority", "Status"),
        )
        self._occupations_tab = self._make_diff_tab(
            headers=("", "MARC record", "Authority", "Status"),
        )
        # Raw tab: side-by-side split with per-line similarity
        # highlighting. Left = MARC, right = Authority.
        self._raw_tab = QWidget()
        raw_layout = QHBoxLayout(self._raw_tab)
        raw_layout.setContentsMargins(0, 0, 0, 0)
        raw_layout.setSpacing(theme.SPACE_SM)

        raw_pane_qss = (
            f"QTextEdit {{ background: rgba(0,0,0, 90);"
            f" color: {theme.ui('text')}; border: 1px solid rgba(255,255,255, 22);"
            f" border-radius: {theme.RADIUS_MD}px;"
            f" font-family: 'SF Mono', Menlo, Consolas, monospace;"
            f" font-size: {theme.FONT_SM}px;"
            f" padding: 8px; }}"
        )
        self._raw_left = QTextEdit()
        self._raw_left.setReadOnly(True)
        self._raw_left.setAcceptRichText(True)
        self._raw_left.setStyleSheet(raw_pane_qss)
        self._raw_right = QTextEdit()
        self._raw_right.setReadOnly(True)
        self._raw_right.setAcceptRichText(True)
        self._raw_right.setStyleSheet(raw_pane_qss)
        raw_layout.addWidget(self._raw_left, stretch=1)
        raw_layout.addWidget(self._raw_right, stretch=1)

        self._tabs.addTab(self._dates_tab, "Dates")
        self._tabs.addTab(self._places_tab, "Places")
        self._tabs.addTab(self._names_tab, "Names / variants")
        self._tabs.addTab(self._occupations_tab, "Occupations")
        self._tabs.addTab(self._raw_tab, "Raw")

        # Footer — Approve + Next + Close
        bar = QHBoxLayout()
        bar.addStretch()

        self._approve_btn = QPushButton("✓ Approve")
        self._approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._approve_btn.setStyleSheet(theme.button_style("success"))
        self._approve_btn.setToolTip(
            "Mark this match approved and keep the dialog open so you can "
            "advance to the next row without losing context."
        )
        self._approve_btn.clicked.connect(self._handle_approve)
        bar.addWidget(self._approve_btn)

        self._next_btn = QPushButton("→ Next")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet(theme.button_style("primary"))
        self._next_btn.setToolTip("Load the next match into this same dialog.")
        self._next_btn.clicked.connect(self._handle_next)
        bar.addWidget(self._next_btn)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(theme.button_style())
        close_btn.clicked.connect(self.accept)
        bar.addWidget(close_btn)
        outer.addLayout(bar)

        # Refresh Approve button state so it reflects the starting row's
        # current approval flag.
        self._refresh_approve_button()

        if comparison is not None:
            self.show_comparison(comparison)

    def _refresh_header(self) -> None:
        theme = self._theme
        row = self._row
        source = row.get("source") or ""
        entity = row.get("entity_text") or ""
        match_name = row.get("matched_name") or ""
        matched_id = row.get("matched_id") or ""
        approved = bool(row.get("approved"))
        badge = (
            f"<span style='background:rgba(34,197,94,180);"
            f" color:white; padding:2px 8px;"
            f" border-radius:{theme.RADIUS_SM}px; font-size:{theme.FONT_XS}px;"
            f" margin-left:{theme.SPACE_SM}px'>APPROVED</span>"
            if approved else ""
        )
        self._header_label.setText(
            f"<div style='font-size:{theme.FONT_LG}px; font-weight:600;"
            f" color:{theme.ui('text')}'>{entity}{badge}</div>"
            f"<div style='color:{theme.ui('subtext')};"
            f" font-size:{theme.FONT_SM}px; margin-top:4px'>"
            f"Source: <b>{source}</b> · "
            f"Match: <b>{match_name}</b>"
            + (f" (<code>{matched_id}</code>)" if matched_id else "")
            + "</div>",
        )
        self.setWindowTitle(
            f"Compare — {entity}  ↔  {match_name or matched_id}"
        )

    def _refresh_approve_button(self) -> None:
        if self._row.get("approved"):
            self._approve_btn.setText("✓ Approved")
            self._approve_btn.setEnabled(False)
        else:
            self._approve_btn.setText("✓ Approve")
            self._approve_btn.setEnabled(True)

    def _handle_approve(self) -> None:
        if self._on_approve is None:
            return
        self._on_approve(self._row)
        # Flip local state + refresh the badge
        self._row["approved"] = True
        self._refresh_header()
        self._refresh_approve_button()

    def _handle_next(self) -> None:
        if self._on_next is None:
            self.accept()
            return
        next_state = self._on_next(self._row)
        if not next_state:
            # Nothing after this row — close
            self.accept()
            return
        self.load_row(next_state["row"], next_state.get("comparison"))
        # If an async fetch was kicked off, the caller will call
        # show_comparison() a second time when it resolves; until then
        # the progress bar indicates activity.
        if next_state.get("show_progress"):
            self._progress.setVisible(True)

    def load_row(self, row: dict, comparison: BioComparison | None = None) -> None:
        """Swap the dialog's content to show a different match.

        Called by the navigation handler after the user clicks → Next.
        Resets all tabs, header badge, and Approve button state.
        """
        self._row = row
        self._refresh_header()
        self._refresh_approve_button()
        self._dates_tab.clear()
        self._places_tab.clear()
        self._names_tab.clear()
        self._occupations_tab.clear()
        self._raw_left.setPlainText("")
        self._raw_right.setPlainText("")
        if comparison is not None:
            self.show_comparison(comparison)

    def _make_diff_tab(self, *, headers: tuple[str, ...]) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setColumnCount(len(headers))
        tree.setHeaderLabels(list(headers))
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setStyleSheet(glass_table_style(self._theme))
        tree.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        return tree

    # Public API -----------------------------------------------------------

    def show_comparison(self, cmp_: BioComparison) -> None:
        """Render the tabs from a ready :class:`BioComparison`.

        Idempotent: safe to call twice (e.g., once synchronously with the
        MARC side + empty authority, once again when the async authority
        fetch resolves). Subsequent calls replace tab contents.
        """
        self._progress.setVisible(False)
        self._populate_dates(cmp_)
        self._populate_places(cmp_)
        self._populate_names(cmp_)
        self._populate_occupations(cmp_)
        self._populate_raw(cmp_)

    def show_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._raw_right.setPlainText(f"Fetch failed: {message}")
        self._tabs.setCurrentWidget(self._raw_tab)

    # Internals ------------------------------------------------------------

    _VERDICT_COLOURS = {
        "matched":            ("#dcfce7", "#14532d"),
        "differs":            ("#fef3c7", "#78350f"),
        "only-in-marc":       ("#e0e7ff", "#312e81"),
        "only-in-authority":  ("#fdf2f8", "#701a75"),
    }

    def _add_row(
        self,
        tree: QTreeWidget,
        field_label: str,
        marc_val: str,
        auth_val: str,
        verdict: str,
    ) -> None:
        item = QTreeWidgetItem([field_label, marc_val or "—", auth_val or "—", verdict])
        bg, fg = self._VERDICT_COLOURS.get(verdict, ("#f3f4f6", "#374151"))
        from PyQt6.QtGui import QBrush, QColor  # noqa: PLC0415
        item.setBackground(3, QBrush(QColor(bg)))
        item.setForeground(3, QBrush(QColor(fg)))
        tree.addTopLevelItem(item)

    def _populate_dates(self, cmp_: BioComparison) -> None:
        self._dates_tab.clear()
        keys = sorted(set(cmp_.marc.dates) | set(cmp_.authority.dates))
        if not keys:
            self._add_row(self._dates_tab, "", "", "", "no data")
            return
        for k in keys:
            m = cmp_.marc.dates.get(k, "")
            a = cmp_.authority.dates.get(k, "")
            if m and a:
                verdict = "matched" if _norm(m) == _norm(a) else "differs"
            elif m:
                verdict = "only-in-marc"
            else:
                verdict = "only-in-authority"
            self._add_row(self._dates_tab, k, m, a, verdict)

    def _populate_places(self, cmp_: BioComparison) -> None:
        self._places_tab.clear()
        keys = sorted(set(cmp_.marc.places) | set(cmp_.authority.places))
        if not keys:
            self._add_row(self._places_tab, "", "", "", "no data")
            return
        for k in keys:
            m = cmp_.marc.places.get(k, [])
            a = cmp_.authority.places.get(k, [])
            for marc_v, auth_v, verdict in _diff_pairs(m, a):
                self._add_row(self._places_tab, k, marc_v, auth_v, verdict)

    def _populate_names(self, cmp_: BioComparison) -> None:
        self._names_tab.clear()
        keys = sorted(set(cmp_.marc.names) | set(cmp_.authority.names))
        if not keys:
            self._add_row(self._names_tab, "", "", "", "no data")
            return
        for lang in keys:
            m = cmp_.marc.names.get(lang, [])
            a = cmp_.authority.names.get(lang, [])
            for marc_v, auth_v, verdict in _diff_pairs(m, a):
                self._add_row(self._names_tab, lang, marc_v, auth_v, verdict)

    def _populate_occupations(self, cmp_: BioComparison) -> None:
        self._occupations_tab.clear()
        for marc_v, auth_v, verdict in _diff_pairs(
            cmp_.marc.occupations, cmp_.authority.occupations,
        ):
            self._add_row(self._occupations_tab, "occupation", marc_v, auth_v, verdict)
        if self._occupations_tab.topLevelItemCount() == 0:
            self._add_row(self._occupations_tab, "", "", "", "no data")

    def _populate_raw(self, cmp_: BioComparison) -> None:
        """Render MARC + Authority side-by-side as pretty JSON.

        Highlight rules (per user request 2026-04-25):

        * Match the *value content* of each line, ignoring the key —
          so MARC's ``"occupations": ["author"]`` line and Authority's
          ``"notes": ["role: author"]`` line BOTH highlight because
          the scalar "author" appears on both sides, even under
          different keys. Cross-key matches are the valuable signal.
        * Structural lines (``{``, ``}``, ``[``, ``]``, ``"k": {}``,
          ``"k": []``, trailing commas) are *ignored* — they always
          match trivially on both sides and adding them as highlights
          drowns out the useful signal.
        * Still use a ≥ 0.90 similarity threshold on the extracted
          value content.
        """
        import html              # noqa: PLC0415
        import json              # noqa: PLC0415
        import re as _re         # noqa: PLC0415
        from difflib import SequenceMatcher  # noqa: PLC0415

        def to_dict(b: BioData) -> dict:
            return {
                "dates": b.dates, "places": b.places, "names": b.names,
                "occupations": b.occupations, "notes": b.notes,
            }

        marc_json = json.dumps(
            to_dict(cmp_.marc), indent=2, ensure_ascii=False, default=str,
        )
        auth_json = json.dumps(
            to_dict(cmp_.authority), indent=2, ensure_ascii=False, default=str,
        )
        marc_lines = marc_json.splitlines() or [""]
        auth_lines = auth_json.splitlines() or [""]

        def _extract_value(line: str) -> str:
            """Return the *value* part of a pretty-JSON line, stripped of
            keys, quotes, trailing commas, enclosing brackets. Empty if
            the line is purely structural."""
            s = line.strip()
            # Pure brackets / separators
            if s in ("{", "}", "[", "]", "{}", "[]", ",", ""):
                return ""
            # "key": {},  or  "key": [],  or  "key": {   or  "key": [
            if _re.match(
                r'^"[^"]+"\s*:\s*[\{\[]\s*[\}\]]?\s*,?\s*$', s,
            ):
                return ""
            # Drop a leading "key":
            m = _re.match(r'^"[^"]+"\s*:\s*(.*)$', s)
            if m:
                s = m.group(1)
            # Strip trailing comma
            s = s.rstrip(",")
            s = s.strip()
            # Strip enclosing quotes on string scalars
            if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
                s = s[1:-1]
            return s.strip()

        def _norm(s: str) -> str:
            return s.strip().casefold()

        marc_vals = [_extract_value(line) for line in marc_lines]
        auth_vals = [_extract_value(line) for line in auth_lines]

        # For each line on each side, find the best cross-side value
        # match. Both sides contribute, so a match lights up both
        # lines simultaneously regardless of which key they live under.
        marc_matched: set[int] = set()
        auth_matched: set[int] = set()
        for i, m in enumerate(marc_vals):
            if not m:
                continue
            m_norm = _norm(m)
            if len(m_norm) < 2:
                continue
            for j, a in enumerate(auth_vals):
                if not a:
                    continue
                a_norm = _norm(a)
                if len(a_norm) < 2:
                    continue
                # Fast path: exact match bypasses SequenceMatcher.
                if m_norm == a_norm:
                    marc_matched.add(i)
                    auth_matched.add(j)
                    continue
                if SequenceMatcher(None, m_norm, a_norm).ratio() >= 0.90:
                    marc_matched.add(i)
                    auth_matched.add(j)

        hl_bg = "rgba(59,130,246,70)"
        hl_fg = "rgba(147,197,253,255)"

        def _render(lines: list[str], matched: set[int]) -> str:
            out_lines: list[str] = [
                "<pre style='margin:0;"
                " font-family:SF Mono,Menlo,Consolas,monospace;"
                " white-space:pre-wrap; word-break:break-word'>",
            ]
            for i, line in enumerate(lines):
                escaped = html.escape(line) or "&nbsp;"
                if i in matched:
                    out_lines.append(
                        f"<div style='background:{hl_bg}; color:{hl_fg}'>"
                        f"{escaped}</div>",
                    )
                else:
                    out_lines.append(f"<div>{escaped}</div>")
            out_lines.append("</pre>")
            return "".join(out_lines)

        self._raw_left.setHtml(_render(marc_lines, marc_matched))
        self._raw_right.setHtml(_render(auth_lines, auth_matched))


__all__ = [
    "MatchComparisonDialog",
    "fetch_biodata_async",
    "_cache_get",
    "_cache_put",
]
