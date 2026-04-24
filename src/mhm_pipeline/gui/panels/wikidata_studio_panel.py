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
    QFileDialog,
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


class _ValidationWorker(QThread):
    """Full validate-with-Wikidata pass over the built items.

    Runs every live check the pipeline supports: duplicate-detection via
    the reconciler, identifier-collision, item-existence SPARQL ASK,
    three-channel creator verification, and a re-run of
    ``item_validator.validate_item()`` against the possibly-edited item.

    Emits a full per-row payload on :attr:`row_validated`, so the
    status-info popup has every signal it needs without another network
    round-trip. The legacy :attr:`status` 4-tuple stays for backward
    compatibility with earlier callers.
    """

    # Per-row full payload — drives the ℹ status popup.
    row_validated = pyqtSignal(str, dict)
    # Legacy thin signal kept for backward compat with callers that only
    # care about (local_id, status, qid, reason).
    status = pyqtSignal(str, str, str, str)
    progress = pyqtSignal(int)                # 0–100 percent
    log_line = pyqtSignal(str)                # live-log passthrough
    finished_all = pyqtSignal(dict)           # summary counters
    failed = pyqtSignal(str)

    _LOG_EVERY = 50   # running-counts log line every N items
    _PROGRESS_EVERY = 1

    _ID_PIDS = ("P214", "P8189", "P244", "P227", "P213")

    def __init__(
        self,
        items: list[Any],
        token: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = items
        self._token = token
        self._cancelled = False

    def cancel(self) -> None:
        """Request a graceful stop — checked between items."""
        self._cancelled = True

    def run(self) -> None:
        try:
            import sys as _sys  # noqa: PLC0415
            import time as _time  # noqa: PLC0415
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
            if auth_user:
                self.log_line.emit(f"Authenticated as Wikidata user: {auth_user}")
            else:
                self.log_line.emit(
                    "Anonymous session — creator-verification will be "
                    "best-effort only (no bot token provided).",
                )

            total = len(self._items)
            counts = {"new": 0, "ours": 0, "other": 0}
            started = _time.monotonic()
            self.progress.emit(0)

            def _tally(status: str) -> None:
                if status == _STATUS_NEW:
                    counts["new"] += 1
                elif status == _STATUS_OURS:
                    counts["ours"] += 1
                elif status == _STATUS_OTHER:
                    counts["other"] += 1

            from converter.wikidata.item_validator import validate_item  # noqa: PLC0415
            from datetime import datetime, UTC  # noqa: PLC0415

            for idx, item in enumerate(self._items, start=1):
                if self._cancelled:
                    self.log_line.emit(f"Check cancelled at item {idx}/{total}.")
                    break

                row_start = _time.monotonic()
                local_id = str(getattr(item, "local_id", "") or "")
                etype = getattr(item, "entity_type", "")
                existing = getattr(item, "existing_qid", "") or ""

                # --- Re-run in-process validator on the (possibly edited) item
                validator_issues = validate_item(item)

                # --- Per-identifier collision table
                id_checks: dict[str, dict] = {}
                proposed_ids: dict[str, str] = {}
                for s in getattr(item, "statements", []) or []:
                    pid = getattr(s, "property_id", "")
                    if pid in self._ID_PIDS:
                        proposed_ids[pid] = str(getattr(s, "value", "") or "")
                for pid, val in proposed_ids.items():
                    try:
                        owner = reconciler.reconcile_person_by_external_id(pid, val)
                    except Exception:  # noqa: BLE001
                        owner = None
                    if not owner:
                        verdict = "not-found"
                    elif existing and owner == existing:
                        verdict = "matched"
                    elif existing and owner != existing:
                        verdict = "conflict"
                    else:
                        verdict = "matched"
                    id_checks[pid] = {
                        "proposed": val, "existing": owner or "", "verdict": verdict,
                    }

                # --- Overall reconcile match for persons
                match_qid: str | None = None
                labels = getattr(item, "labels", {}) or {}
                name = labels.get("he") or labels.get("en") or ""
                if etype == "person":
                    try:
                        match_qid = reconciler.reconcile_person(
                            name=name,
                            viaf_uri=proposed_ids.get("P214"),
                            nli_id=proposed_ids.get("P8189"),
                            lc_id=proposed_ids.get("P244"),
                            gnd_id=proposed_ids.get("P227"),
                            isni=proposed_ids.get("P213"),
                        )
                    except Exception as _exc:  # noqa: BLE001
                        logger.debug("reconcile_person error for %s: %s", name, _exc)

                # --- Target QID to verify (existing beats freshly matched)
                target_qid = existing or match_qid or ""

                # --- Three-channel creator check (only if we have a target)
                creator_check: dict | None = None
                sparql_ask = ""
                sparql_answer: bool | None = None
                if target_qid:
                    rev_author = uploader._get_first_revision_author(target_qid) or ""
                    contribs_new: bool | None = None
                    if auth_user:
                        contribs_new = uploader._user_created_via_contribs(
                            target_qid, auth_user,
                        )
                    sparql_answer = uploader._item_exists_on_wikidata_sparql(target_qid)
                    sparql_ask = f"ASK WHERE {{ wd:{target_qid} ?p ?o . }}"
                    if not auth_user:
                        verdict = "unknown-auth"
                    elif rev_author == auth_user and contribs_new is not False:
                        verdict = "ours"
                    elif rev_author and rev_author != auth_user:
                        verdict = "other"
                    else:
                        verdict = "unverified"
                    creator_check = {
                        "auth_user": auth_user,
                        "first_rev_author": rev_author,
                        "contribs_new": contribs_new,
                        "verdict": verdict,
                    }

                # --- Collapse into a status constant
                if target_qid:
                    if sparql_answer is False:
                        status_final = _STATUS_OTHER  # item vanished — block
                        reason = (
                            f"SPARQL ASK returned false — {target_qid} has no "
                            "triples (deleted / redirected / blanked)"
                        )
                    elif creator_check and creator_check["verdict"] == "ours":
                        status_final = _STATUS_OURS
                        reason = (
                            f"First revision by {creator_check['first_rev_author']}"
                        )
                    elif creator_check and creator_check["verdict"] == "other":
                        status_final = _STATUS_OTHER
                        reason = (
                            f"First revision by "
                            f"{creator_check['first_rev_author'] or 'unknown'} "
                            f"(not the authenticated user)"
                        )
                    else:
                        status_final = _STATUS_UNKNOWN
                        reason = "Creator could not be verified"
                elif etype != "person":
                    status_final = _STATUS_NEW
                    reason = "No reconciliation available for non-person"
                else:
                    status_final = _STATUS_NEW
                    reason = "No match on Wikidata"

                # --- Identifier-collision downgrade
                had_conflict = any(
                    c.get("verdict") == "conflict" for c in id_checks.values()
                )
                if had_conflict and status_final == _STATUS_NEW:
                    status_final = _STATUS_OTHER
                    reason += " · IDENTIFIER CONFLICT with existing item(s)"

                latency_ms = int((_time.monotonic() - row_start) * 1000)

                payload = {
                    "status": status_final,
                    "status_reason": reason,
                    "matched_qid": target_qid,
                    "identifier_checks": id_checks,
                    "label_collision": None,  # reserved for future extension
                    "sparql_ask": sparql_ask,
                    "sparql_answer": sparql_answer,
                    "creator_check": creator_check,
                    "validator_issues": validator_issues,
                    "validated_at": datetime.now(UTC).isoformat(),
                    "validation_latency_ms": latency_ms,
                }
                self.row_validated.emit(local_id, payload)
                # Legacy thin signal for callers that only want status
                self.status.emit(local_id, status_final, target_qid, reason)

                _tally(status_final)

                # Surface safety-critical events immediately
                if status_final == _STATUS_OTHER:
                    self.log_line.emit(
                        f"[{idx}/{total}] blocked — {local_id} → {target_qid} · {reason}"
                    )
                elif had_conflict:
                    self.log_line.emit(
                        f"[{idx}/{total}] identifier conflict on {local_id}: "
                        + ", ".join(
                            f"{pid}={c['proposed']} owned by {c['existing']}"
                            for pid, c in id_checks.items()
                            if c.get("verdict") == "conflict"
                        )
                    )

                # Progress update every item; summary log every _LOG_EVERY
                if idx % self._PROGRESS_EVERY == 0 or idx == total:
                    pct = int(idx * 100 / total) if total else 100
                    self.progress.emit(pct)
                if idx % self._LOG_EVERY == 0 or idx == total:
                    elapsed = _time.monotonic() - started
                    rate = idx / elapsed if elapsed > 0 else 0.0
                    eta = (total - idx) / rate if rate > 0 else 0.0
                    self.log_line.emit(
                        f"[{idx}/{total}] new={counts['new']} · "
                        f"ours={counts['ours']} · blocked={counts['other']} · "
                        f"{rate:.1f} items/s · ETA {int(eta)}s"
                    )

            self.progress.emit(100)
            self.log_line.emit(
                f"Validation complete: {counts['new']} new · {counts['ours']} ours · "
                f"{counts['other']} blocked (existing, not ours)"
            )
            self.finished_all.emit(dict(counts))
        except Exception as exc:  # noqa: BLE001
            logger.error("Validation failed: %s", exc, exc_info=True)
            self.failed.emit(str(exc))


# Backward-compat alias — older imports still use the old name.
_DuplicateCheckWorker = _ValidationWorker


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
        self._check_btn = QPushButton("Validate with Wikidata")
        self._check_btn.setStyleSheet(theme.button_style("load"))
        self._check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_btn.setEnabled(False)
        self._check_btn.setToolTip(
            "Runs the full live validation pass per item:\n"
            "  • duplicate detection (VIAF / NLI / LCCN / GND / ISNI)\n"
            "  • per-identifier collision check against the live ID owner\n"
            "  • SPARQL ASK existence check on matched QIDs\n"
            "  • three-channel creator verification (revisions, contribs, SPARQL)\n"
            "  • re-run of in-process validator against the current item\n"
            "Updates each row's status + info-icon with the full payload."
        )
        self._check_btn.clicked.connect(self._on_validate)
        action_row.addWidget(self._check_btn)

        self._save_validation_btn = QPushButton("💾 Save validation")
        self._save_validation_btn.setStyleSheet(theme.button_style("success"))
        self._save_validation_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_validation_btn.setEnabled(False)
        self._save_validation_btn.setToolTip(
            "Save the current items + validation payloads to a JSON file "
            "that round-trips with the uploader."
        )
        self._save_validation_btn.clicked.connect(self._on_save_validation)
        action_row.addWidget(self._save_validation_btn)

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

    # ── Step 4: validate with Wikidata ─────────────────────────────────

    def _on_validate(self) -> None:
        items = self._qp_browser.all_items()
        if not items:
            QMessageBox.information(self, "Nothing to validate", "Load + build items first.")
            return
        token = self._token_edit.text().strip()
        if not token:
            reply = QMessageBox.question(
                self, "No bot password",
                "Creator-verification and contribs cross-check need an "
                "authenticated session. Proceed anonymously anyway?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._update_stepper(step=4)
        self._check_btn.setEnabled(False)
        self._save_validation_btn.setEnabled(False)
        self._progress.set_progress(0)
        self._progress.setVisible(True)
        self._log_viewer.append_line(
            f"Validating {len(items)} items against Wikidata…"
        )
        self._check_worker = _ValidationWorker(items, token, parent=self)
        # New richer signal — full payload per row.
        self._check_worker.row_validated.connect(self._on_row_validated)
        self._check_worker.progress.connect(self._progress.set_progress)
        self._check_worker.log_line.connect(self._log_viewer.append_line)
        self._check_worker.finished_all.connect(self._on_validation_done)
        self._check_worker.failed.connect(self._on_check_failed)
        self._check_worker.start()

    # Backward-compat alias for the old handler name
    _on_check_duplicates = _on_validate

    def _on_row_validated(self, local_id: str, payload: dict) -> None:
        self._qp_browser.update_validation(local_id, payload)

    def _on_validation_done(self, summary: dict) -> None:
        self._progress.set_progress(100)
        self._check_btn.setEnabled(True)
        self._save_validation_btn.setEnabled(True)
        self._update_stepper(step=5)
        if self._check_worker is not None:
            self._check_worker.wait()
            self._check_worker = None
        self._log_viewer.append_line(
            f"Validation complete: {summary.get('new', 0)} new · "
            f"{summary.get('ours', 0)} ours · "
            f"{summary.get('other', 0)} blocked."
        )

    # Backward-compat alias
    _on_check_done = _on_validation_done

    def _on_check_failed(self, message: str) -> None:
        self._log_viewer.append_line(f"Validation failed: {message}")
        QMessageBox.critical(self, "Validation failed", message)

    # ── Save validation results ────────────────────────────────────────

    def _on_save_validation(self) -> None:
        """Serialise the checked-items table (with full validation payload)
        to a JSON file. Round-trips with the uploader's input format."""
        rows = self._qp_browser.rows_snapshot()
        if not rows:
            QMessageBox.information(self, "Nothing to save", "No items to save.")
            return

        default_dir = self._output_selector.path or (
            self._input_selector.path.parent if self._input_selector.path else Path.home()
        )
        default_path = str(Path(default_dir) / "wikidata_validation_result.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save validation results", default_path, "JSON files (*.json)",
        )
        if not path:
            return

        from mhm_pipeline.gui.widgets.qp_entity_browser import (  # noqa: PLC0415
            serialize_validated_rows,
        )
        import json  # noqa: PLC0415

        try:
            out = serialize_validated_rows(rows)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2, default=str)
            self._log_viewer.append_line(
                f"Saved {len(out)} validated items → {path}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to save validation: %s", exc, exc_info=True)
            QMessageBox.critical(self, "Save failed", str(exc))
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
