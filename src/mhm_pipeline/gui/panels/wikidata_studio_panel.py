"""Wikidata Studio — unified review/check/approve/upload surface.

Replaces the separate WikidataPreviewPanel (Stage 3 review) and
WikidataPanel (Stage 6 upload) with one progressive workflow:

    Load authority_enriched.json
        ↓
    Build Wikidata items (offline via WikidataItemBuilder)
        ↓
    Browse
        · Q/P entity browser (editable, approvable rows per item)
        · Raw RDF triples (Turtle preview + interactive graph)
        ↓
    Check live Wikidata for duplicates
        ↓
    Auto-approve + bulk-approve
        ↓
    Export: QuickStatements (dry run) or Upload via WikidataUploader

Design follows the NN/g "progressive form" pattern: a visible stepper at
top, all sections reachable via scroll, approval gates the next actions.

Only the approved subset flows downstream — mirrors the NER and Authority
approve-before-stages we built earlier.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.percent_progress import PercentProgressWidget
from mhm_pipeline.gui.widgets.qp_entity_browser import (
    _STATUS_NEW,
    _STATUS_OTHER,
    _STATUS_OURS,
    QPEntityBrowser,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Background workers (non-blocking RDF build + duplicate check)
# ────────────────────────────────────────────────────────────────────────────


class _BuildWorker(QThread):
    """Runs WikidataItemBuilder.build_all in the background."""

    finished_items = pyqtSignal(list)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int)

    def __init__(self, records: list[dict], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._records = records

    def run(self) -> None:
        try:
            # Ensure the repo root is on sys.path (app-bundle layout)
            import sys as _sys  # noqa: PLC0415
            from pathlib import Path as _Path  # noqa: PLC0415

            _repo = _Path(__file__).resolve().parents[4]
            if str(_repo) not in _sys.path:
                _sys.path.insert(0, str(_repo))

            from converter.wikidata.item_builder import WikidataItemBuilder  # noqa: PLC0415

            builder = WikidataItemBuilder(reconciler=None)  # offline

            def _cb(n_done: int, n_total: int) -> None:
                pct = int(n_done / max(1, n_total) * 100)
                self.progress.emit(pct)

            items = builder.build_all(self._records, progress_cb=_cb)
            self.finished_items.emit(list(items))
        except Exception as exc:  # noqa: BLE001
            logger.error("Wikidata build failed: %s", exc, exc_info=True)
            self.failed.emit(str(exc))


class _DuplicateCheckWorker(QThread):
    """Queries Wikidata to classify each item as new / ours / others."""

    status = pyqtSignal(str, str, str, str)   # (local_id, status, qid, reason)
    finished_all = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(
        self,
        items: list[Any],
        token: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = items
        self._token = token

    def run(self) -> None:
        try:
            import sys as _sys  # noqa: PLC0415
            from pathlib import Path as _Path  # noqa: PLC0415

            _repo = _Path(__file__).resolve().parents[4]
            if str(_repo) not in _sys.path:
                _sys.path.insert(0, str(_repo))
            if str(_repo / "src") not in _sys.path:
                _sys.path.insert(0, str(_repo / "src"))

            from converter.wikidata.reconciler import WikidataReconciler  # noqa: PLC0415
            from converter.wikidata.uploader import WikidataUploader  # noqa: PLC0415

            reconciler = WikidataReconciler()
            # Read-only: we only call _get_authenticated_user() and
            # _get_first_revision_author() — both hit the MediaWiki API
            # but never write. The moratorium gate only fires on
            # upload_item(), so construction is safe without it lifted.
            uploader = WikidataUploader(token=self._token)
            auth_user = uploader._get_authenticated_user() or ""

            for item in self._items:
                local_id = str(getattr(item, "local_id", "") or "")
                # Only persons are currently reconciled by identifier.
                # Works & manuscripts → stay as "new" unless existing_qid set.
                etype = getattr(item, "entity_type", "")
                existing = getattr(item, "existing_qid", "") or ""

                if existing:
                    creator = uploader._get_first_revision_author(existing) or ""
                    if auth_user and creator == auth_user:
                        self.status.emit(local_id, _STATUS_OURS, existing,
                                         f"First revision by {creator}")
                    else:
                        self.status.emit(local_id, _STATUS_OTHER, existing,
                                         f"First revision by {creator or 'unknown'}")
                    continue

                if etype != "person":
                    self.status.emit(local_id, _STATUS_NEW, "",
                                     "No reconciliation available for non-person")
                    continue

                # Pull identifiers off the person item's statements
                ids = {}
                for s in getattr(item, "statements", []) or []:
                    pid = getattr(s, "property_id", "")
                    val = str(getattr(s, "value", "") or "")
                    if pid == "P214":
                        ids["viaf_uri"] = val
                    elif pid == "P8189":
                        ids["nli_id"] = val
                    elif pid == "P244":
                        ids["lc_id"] = val
                    elif pid == "P227":
                        ids["gnd_id"] = val
                    elif pid == "P213":
                        ids["isni"] = val

                qid: str | None = None
                labels = getattr(item, "labels", {}) or {}
                name = labels.get("he") or labels.get("en") or ""
                try:
                    qid = reconciler.reconcile_person(
                        name=name,
                        viaf_uri=ids.get("viaf_uri"),
                        nli_id=ids.get("nli_id"),
                        lc_id=ids.get("lc_id"),
                        gnd_id=ids.get("gnd_id"),
                        isni=ids.get("isni"),
                    )
                except Exception as _exc:  # noqa: BLE001
                    logger.debug("reconcile_person error for %s: %s", name, _exc)

                if not qid:
                    self.status.emit(local_id, _STATUS_NEW, "", "No match on Wikidata")
                    continue

                creator = uploader._get_first_revision_author(qid) or ""
                if auth_user and creator == auth_user:
                    self.status.emit(local_id, _STATUS_OURS, qid,
                                     f"First revision by {creator}")
                else:
                    self.status.emit(local_id, _STATUS_OTHER, qid,
                                     f"First revision by {creator or 'unknown'}")
            self.finished_all.emit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Duplicate check failed: %s", exc, exc_info=True)
            self.failed.emit(str(exc))


# ────────────────────────────────────────────────────────────────────────────
# Main panel
# ────────────────────────────────────────────────────────────────────────────


class WikidataStudioPanel(QWidget):
    """Unified Wikidata review + check + approve + upload surface."""

    # (input_path, output_dir, token, dry_run, batch_mode, approved_items)
    upload_requested = pyqtSignal(Path, Path, str, bool, bool, list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        content = QWidget()
        layout = QVBoxLayout(content)
        # Generous margins + inter-section spacing so the stepper, input
        # rows, tabs and action bar each get a clear breathing band rather
        # than feeling packed together.
        layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        layout.setSpacing(theme.SPACE_LG)

        # ── Stepper (progress indicator) ──────────────────────────────
        self._stepper = QLabel()
        self._stepper.setTextFormat(Qt.TextFormat.RichText)
        self._stepper.setStyleSheet(
            f"font-size: {theme.FONT_MD}px; color: {theme.ui('subtext')};"
            f" padding: 4px 6px;"
        )
        layout.addWidget(self._stepper)
        self._update_stepper(step=1)

        # ── Step 1: Load authority_enriched.json ───────────────────────
        load_row = QHBoxLayout()
        load_row.setSpacing(theme.SPACE_MD)
        self._input_selector = FileSelector(
            "Authority JSON:", mode="open", filter="JSON files (*.json)",
        )
        self._input_selector.setToolTip(
            "authority_enriched.json — Stage 2 output containing MARC + all-5-NER + "
            "authority data (VIAF/Mazal/KIMA). The studio builds Wikidata-shaped "
            "items from this file entirely offline."
        )
        load_row.addWidget(self._input_selector, stretch=1)
        self._load_btn = QPushButton("Load & Build Items")
        self._load_btn.setStyleSheet(theme.button_style("primary"))
        self._load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_btn.clicked.connect(self._on_load_and_build)
        load_row.addWidget(self._load_btn)
        layout.addLayout(load_row)

        # ── Output dir (for QuickStatements + upload-result JSON) ─────
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        self._output_selector.setToolTip(
            "Destination directory for QuickStatements export (dry run) or "
            "upload_results.json (live upload)."
        )
        layout.addWidget(self._output_selector)

        # ── Progress bar for background tasks ─────────────────────────
        self._progress = PercentProgressWidget()
        layout.addWidget(self._progress)

        # ── Browse: Q/P browser + RDF triples view in two tabs ─────────
        self._tabs = QTabWidget()
        # The studio lives inside a scroll-area (so nothing gets clipped
        # on narrow windows), which means QHBoxLayout.stretch can't give
        # the tabs the "all remaining vertical space" behaviour you get
        # in a non-scrollable panel. Setting a generous minimum height
        # ensures the table shows at least ~12 rows in the embedded view;
        # the full-screen button below lets the user expand to the whole
        # display for dense review sessions.
        self._tabs.setMinimumHeight(520)

        self._qp_browser = QPEntityBrowser()
        self._qp_browser.items_changed.connect(self._on_items_changed)
        self._tabs.addTab(self._qp_browser, "Q/P Entities")

        self._rdf_preview = QTextEdit()
        self._rdf_preview.setReadOnly(True)
        self._rdf_preview.setAcceptRichText(False)
        self._rdf_preview.setPlaceholderText(
            "Raw Turtle RDF will appear here after items are built. "
            "Use the Q/P Entities tab on the left for per-item review."
        )
        self._tabs.addTab(self._rdf_preview, "RDF Triples")

        # Full-screen corner button on the tab bar (top-right)
        self._fullscreen_btn = QPushButton("⛶ Full screen")
        self._fullscreen_btn.setStyleSheet(theme.button_style("load"))
        self._fullscreen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fullscreen_btn.setToolTip(
            "Open the Q/P Entities + RDF Triples browser in a full-screen "
            "popup for dense review. The embedded view stays in sync."
        )
        self._fullscreen_btn.clicked.connect(self._on_open_fullscreen)
        self._tabs.setCornerWidget(self._fullscreen_btn, Qt.Corner.TopRightCorner)

        layout.addWidget(self._tabs, stretch=1)

        # ── Step 3: Duplicate-check + upload row ───────────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(theme.SPACE_LG)
        self._check_btn = QPushButton("Check Wikidata for duplicates")
        self._check_btn.setStyleSheet(theme.button_style("load"))
        self._check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_btn.setEnabled(False)
        self._check_btn.setToolTip(
            "Queries Wikidata for each person item by VIAF / Mazal / LCCN / "
            "GND / ISNI and marks items as new / existing-ours / "
            "existing-other. Requires bot password for the creator check."
        )
        self._check_btn.clicked.connect(self._on_check_duplicates)
        action_row.addWidget(self._check_btn)

        action_row.addStretch()

        self._dry_run_cb = QCheckBox("Dry run (QuickStatements export)")
        self._dry_run_cb.setChecked(True)
        self._dry_run_cb.setToolTip(
            "Unchecked → live upload via the Wikidata API. Respects the "
            "§25 moratorium unless MORATORIUM_LIFTED=true is set."
        )
        action_row.addWidget(self._dry_run_cb)

        self._batch_cb = QCheckBox("Batch mode (45 items + 30s pause)")
        self._batch_cb.setChecked(True)
        action_row.addWidget(self._batch_cb)

        self._upload_btn = QPushButton("Export / Upload approved")
        self._upload_btn.setStyleSheet(theme.button_style("primary"))
        self._upload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upload_btn.setEnabled(False)
        self._upload_btn.clicked.connect(self._on_upload)
        action_row.addWidget(self._upload_btn)
        layout.addLayout(action_row)

        # ── Credentials panel (always visible, gated by dry-run state) ─
        #
        # The panel is framed in a liquid-glass card so it reads as a
        # first-class studio section rather than a hidden footer row.
        # It stays visible regardless of dry-run state — only the hint
        # banner changes, so the user always sees *where* to enter creds.
        from mhm_pipeline.gui.widgets.glass_dialog import glass_panel_style  # noqa: PLC0415

        self._cred_panel = QFrame()
        self._cred_panel.setObjectName("glassPanel")
        self._cred_panel.setStyleSheet(glass_panel_style(theme))
        cred_layout = QVBoxLayout(self._cred_panel)
        cred_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD,
        )
        cred_layout.setSpacing(theme.SPACE_SM)

        cred_title = QLabel("🔐  Wikidata credentials")
        cred_title.setStyleSheet(
            f"font-size: {theme.FONT_MD}px; font-weight: 600;"
            f" color: {theme.ui('text')};"
        )
        cred_layout.addWidget(cred_title)

        self._cred_hint = QLabel(
            "Not needed for dry-run export (QuickStatements file). "
            "Required for live Wikidata uploads — enter a bot password "
            "or OAuth 2.0 consumer key."
        )
        self._cred_hint.setWordWrap(True)
        self._cred_hint.setStyleSheet(
            f"color: {theme.ui('subtext')}; font-size: {theme.FONT_SM}px;"
        )
        cred_layout.addWidget(self._cred_hint)

        token_row = QHBoxLayout()
        token_row.setSpacing(theme.SPACE_MD)
        token_label = QLabel("Token:")
        token_label.setMinimumWidth(64)
        token_row.addWidget(token_label)
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_edit.setPlaceholderText(
            "eyJ…  ·  User@BotName:password  ·  consumer_key|consumer_secret"
        )
        self._token_edit.setToolTip(
            "Accepted formats:\n"
            "  • JWT bearer (recommended) — eyJ0eXAiOiJKV1Q… (the access token issued\n"
            "      by Special:OAuthConsumerRegistration for an owner-only, confidential\n"
            "      client. No consumer secret exchange needed.)\n"
            "  • Bot password              — User@BotName:password\n"
            "  • OAuth 2.0 owner-only      — consumer_key|consumer_secret\n"
            "  • OAuth 1.0a                 — key|secret|access_token|access_secret"
        )
        token_row.addWidget(self._token_edit, stretch=1)

        self._show_token_cb = QCheckBox("Show")
        self._show_token_cb.setToolTip("Briefly reveal the token text")
        self._show_token_cb.toggled.connect(
            lambda checked: self._token_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password,
            )
        )
        token_row.addWidget(self._show_token_cb)

        cred_layout.addLayout(token_row)

        # Moratorium status — parroted from the §25 environment variable.
        import os  # noqa: PLC0415

        moratorium_on = os.environ.get("MORATORIUM_LIFTED", "").lower() != "true"
        mor_lbl = QLabel(
            (
                "⚠️  Moratorium ACTIVE — live uploads blocked (set "
                "<code>MORATORIUM_LIFTED=true</code> to enable)."
            ) if moratorium_on else
            "✓ Moratorium LIFTED — live uploads are permitted."
        )
        mor_lbl.setTextFormat(Qt.TextFormat.RichText)
        mor_lbl.setWordWrap(True)
        mor_lbl.setStyleSheet(
            f"color: {theme.ui('warning') if moratorium_on else theme.ui('highlight')};"
            f" font-size: {theme.FONT_SM}px;"
        )
        cred_layout.addWidget(mor_lbl)
        layout.addWidget(self._cred_panel)

        # Keep a legacy attribute for callers that still reference
        # ``_token_row`` (but it is now always visible)
        self._token_row = self._cred_panel

        # When dry-run flips, only the hint text changes — panel stays up.
        def _update_cred_hint(checked: bool) -> None:
            if checked:
                self._cred_hint.setText(
                    "Not needed for dry-run export (QuickStatements file). "
                    "Required for live Wikidata uploads."
                )
            else:
                self._cred_hint.setText(
                    "⚠️  LIVE upload mode — enter a bot password or OAuth "
                    "2.0 key before clicking Export / Upload."
                )
        self._dry_run_cb.toggled.connect(_update_cred_hint)

        # ── Log viewer ─────────────────────────────────────────────────
        self._log_viewer = LogViewer()
        self._log_viewer.setMaximumHeight(180)
        layout.addWidget(self._log_viewer)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # ── State ──────────────────────────────────────────────────────
        self._records: list[dict] = []
        self._input_path: Path | None = None
        self._output_path: Path | None = None
        self._build_worker: _BuildWorker | None = None
        self._check_worker: _DuplicateCheckWorker | None = None

    # ── Accessors used by main window / pipeline_controller ───────────

    @property
    def log_viewer(self) -> LogViewer:
        return self._log_viewer

    @property
    def stage_progress(self) -> PercentProgressWidget:
        return self._progress

    # ── Stepper UI ────────────────────────────────────────────────────

    def _update_stepper(self, step: int) -> None:
        steps = ["Load", "Build", "Browse", "Check", "Approve", "Upload"]
        hilite_bg = theme.ui("highlight")
        subtext = theme.ui("subtext")
        parts = []
        for i, s in enumerate(steps, start=1):
            if i < step:
                parts.append(
                    f"<span style='color:{hilite_bg}; font-weight:600'>✓ {s}</span>"
                )
            elif i == step:
                parts.append(
                    f"<span style='color:{hilite_bg}; font-weight:700;"
                    f" text-decoration:underline'>{s}</span>"
                )
            else:
                parts.append(f"<span style='color:{subtext}'>{s}</span>")
        self._stepper.setText(
            f"<span style='color:{subtext}; letter-spacing:1px;"
            f" font-size:{theme.FONT_XS}px'>STEPS:</span> &nbsp; "
            + "&nbsp;·&nbsp; ".join(parts)
        )

    # ── Step 1+2: Load authority_enriched.json + build items ──────────

    def _on_load_and_build(self) -> None:
        path = self._input_selector.path
        if path is None:
            QMessageBox.information(
                self, "Pick a file", "Select the authority_enriched.json to load.",
            )
            return
        self._input_path = path
        try:
            with open(path, encoding="utf-8") as f:
                records = json.load(f)
            if isinstance(records, dict) and "records" in records:
                records = records["records"]
            if not isinstance(records, list):
                raise ValueError("JSON must be a list of records")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Load Error", str(exc))
            return

        self._records = records
        self._log_viewer.append_line(
            f"Loaded {len(records)} records from {path.name}. Building Wikidata items…"
        )
        self._update_stepper(step=2)
        self._progress.set_progress(0)
        self._load_btn.setEnabled(False)

        # Build in a background thread so the UI stays responsive
        self._build_worker = _BuildWorker(records, parent=self)
        self._build_worker.progress.connect(self._progress.set_progress)
        self._build_worker.finished_items.connect(self._on_items_built)
        self._build_worker.failed.connect(self._on_build_failed)
        self._build_worker.start()

    def _on_items_built(self, items: list[Any]) -> None:
        self._log_viewer.append_line(f"Built {len(items)} Wikidata items.")
        self._qp_browser.load_items(items)
        self._update_stepper(step=3)
        self._progress.set_progress(100)
        self._load_btn.setEnabled(True)
        self._check_btn.setEnabled(True)
        self._upload_btn.setEnabled(True)
        # Also build a Turtle preview
        self._refresh_rdf_preview()
        if self._build_worker is not None:
            self._build_worker.wait()
            self._build_worker = None

    def _on_build_failed(self, message: str) -> None:
        self._log_viewer.append_line(f"Build failed: {message}")
        QMessageBox.critical(self, "Build failed", message)
        self._load_btn.setEnabled(True)
        if self._build_worker is not None:
            self._build_worker.wait()
            self._build_worker = None

    def _refresh_rdf_preview(self) -> None:
        """Rebuild the Turtle preview from the currently-loaded records."""
        if not self._records:
            return
        try:
            import sys as _sys  # noqa: PLC0415
            from pathlib import Path as _Path  # noqa: PLC0415

            _repo = _Path(__file__).resolve().parents[4]
            if str(_repo) not in _sys.path:
                _sys.path.insert(0, str(_repo))
            from converter.transformer.mapper import MarcToRdfMapper  # noqa: PLC0415

            mapper = MarcToRdfMapper()
            graph = mapper.map_json_records(self._records)
            ttl_bytes = graph.serialize(format="turtle")
            self._rdf_preview.setPlainText(
                ttl_bytes if isinstance(ttl_bytes, str) else ttl_bytes.decode("utf-8")
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Turtle serialise failed: %s", exc)
            self._rdf_preview.setPlainText(
                f"(could not serialise RDF: {exc})"
            )

    # ── Step 4: duplicate check ───────────────────────────────────────

    def _on_check_duplicates(self) -> None:
        items = self._qp_browser.all_items()
        if not items:
            QMessageBox.information(self, "Nothing to check", "Load + build items first.")
            return
        token = self._token_edit.text().strip()
        if not token:
            reply = QMessageBox.question(
                self, "No bot password",
                "Duplicate-check and creator-verification are most accurate with a "
                "bot password. Proceed anonymously anyway?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._update_stepper(step=4)
        self._check_btn.setEnabled(False)
        self._log_viewer.append_line(
            f"Checking {len(items)} items against Wikidata via SPARQL…"
        )
        self._check_worker = _DuplicateCheckWorker(items, token, parent=self)
        self._check_worker.status.connect(self._on_status)
        self._check_worker.finished_all.connect(self._on_check_done)
        self._check_worker.failed.connect(self._on_check_failed)
        self._check_worker.start()

    def _on_status(self, local_id: str, status: str, qid: str, reason: str) -> None:
        self._qp_browser.update_status(local_id, status, qid=qid, reason=reason)

    def _on_check_done(self) -> None:
        self._log_viewer.append_line("Wikidata check complete.")
        self._check_btn.setEnabled(True)
        self._update_stepper(step=5)
        if self._check_worker is not None:
            self._check_worker.wait()
            self._check_worker = None

    def _on_check_failed(self, message: str) -> None:
        self._log_viewer.append_line(f"Check failed: {message}")
        QMessageBox.critical(self, "Check failed", message)
        self._check_btn.setEnabled(True)
        if self._check_worker is not None:
            self._check_worker.wait()
            self._check_worker = None

    # ── Step 5+6: approve + upload ────────────────────────────────────

    def _on_items_changed(self) -> None:
        """Auto-bump the stepper to the Approve stage on the first tick."""
        self._update_stepper(step=5)

    def _on_upload(self) -> None:
        approved = self._qp_browser.approved_items()
        if not approved:
            QMessageBox.information(
                self, "Nothing approved",
                "Approve at least one item before exporting / uploading.",
            )
            return
        if self._output_selector.path is None:
            if self._input_path is not None:
                self._output_path = self._input_path.parent
            else:
                QMessageBox.information(
                    self, "Pick an output dir",
                    "Choose where to write the QuickStatements / results JSON.",
                )
                return
        else:
            self._output_path = self._output_selector.path
        token = self._token_edit.text().strip()
        dry_run = self._dry_run_cb.isChecked()
        if not dry_run and not token:
            QMessageBox.warning(
                self, "Token required",
                "Live upload requires a bot password or OAuth token.",
            )
            return

        self._update_stepper(step=6)
        self._log_viewer.append_line(
            f"{'Exporting' if dry_run else 'Uploading'} {len(approved)} approved "
            f"items → {self._output_path}"
        )
        # Delegate to whatever upload worker the controller runs — we pass
        # the concrete pre-built + approved items so it doesn't rebuild.
        self.upload_requested.emit(
            self._input_path or Path(),
            self._output_path,
            token,
            dry_run,
            self._batch_cb.isChecked(),
            approved,
        )

    # ── Full-screen popup ─────────────────────────────────────────────

    def _on_open_fullscreen(self) -> None:
        """Open the Q/P + RDF tabs in a maximised modal dialog.

        The same ``self._tabs`` widget is reparented into the dialog for
        the duration of the modal session — this keeps the model state,
        filters, selection and scroll positions intact. On close, the
        widget is restored into the embedded layout.
        """
        # Empty the corner widget slot while the tabs are reparented so
        # Qt doesn't double-free the button.
        self._tabs.setCornerWidget(None, Qt.Corner.TopRightCorner)

        from mhm_pipeline.gui.widgets.graph_backdrop import GraphBackdrop  # noqa: PLC0415

        dialog = QDialog(self)
        dialog.setWindowTitle(
            "Wikidata Studio — Q/P Entities & RDF Triples"
        )
        screen = self.screen()
        if screen is not None:
            geom = screen.availableGeometry()
            dialog.resize(geom.width() * 9 // 10, geom.height() * 9 // 10)
        else:
            dialog.resize(1400, 900)

        # Paint the graph-theory wallpaper behind the dialog content so the
        # glass chips + tabs have something to lens through — matches the
        # main-window backdrop exactly.
        backdrop = GraphBackdrop(parent=dialog)
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(backdrop)

        # Content sits on top of the backdrop via a transparent container.
        content = QWidget(backdrop)
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        backdrop_layout = QVBoxLayout(backdrop)
        backdrop_layout.setContentsMargins(0, 0, 0, 0)
        backdrop_layout.addWidget(content)

        dlg_layout = QVBoxLayout(content)
        dlg_layout.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        dlg_layout.setSpacing(theme.SPACE_MD)

        # Re-parent the tabs into the dialog and bump minimum height
        original_min = self._tabs.minimumHeight()
        self._tabs.setParent(dialog)
        self._tabs.setMinimumHeight(0)      # fill the dialog naturally
        self._tabs.show()
        dlg_layout.addWidget(self._tabs, stretch=1)

        # Close bar
        close_bar = QHBoxLayout()
        close_bar.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(theme.button_style())
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dialog.accept)
        close_bar.addWidget(close_btn)
        dlg_layout.addLayout(close_bar)

        dialog.exec()

        # Restore the tabs back into the embedded layout
        self._tabs.setParent(None)
        self._tabs.setMinimumHeight(original_min)
        # Re-attach before the action row (find its index dynamically)
        parent_layout = self.layout()
        # We know progress widget sits right above the tabs (or where they
        # should be). Simpler: re-create corner button + just re-add widget
        # at the end of the Browse section. The original layout.addWidget
        # order means inserting back at the "before _token_row" index is
        # the correct spot, but both are children of the scrolled content's
        # layout. We therefore keep the tabs widget at the end of the
        # content body via the scroll-area contents widget.
        scroll_area = self.findChild(QScrollArea)
        if scroll_area is not None:
            inner = scroll_area.widget()
            inner_layout = inner.layout() if inner is not None else None
            if isinstance(inner_layout, QVBoxLayout):
                # Find the token row / log-viewer index and insert above it
                for i in range(inner_layout.count()):
                    w = inner_layout.itemAt(i).widget()
                    if w is getattr(self, "_token_row", None):
                        inner_layout.insertWidget(i, self._tabs, 1)
                        break
                else:
                    inner_layout.addWidget(self._tabs, 1)

        # Restore corner button
        self._tabs.setCornerWidget(self._fullscreen_btn, Qt.Corner.TopRightCorner)
        self._fullscreen_btn.show()

    # ── Back-compat: pipeline_controller may call display_entities /
    #    show_review_banner on legacy panels. Silent no-ops here.

    def show_review_banner(self) -> None:
        pass
