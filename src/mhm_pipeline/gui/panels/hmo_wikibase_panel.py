"""Optional HMO Wikibase export panel.

This panel builds offline draft entities from the canonical HMO TTL graph
and writes local JSON / QuickStatements artifacts.

Phase 3 (Rule 45, 2026-05-17) added an IIIF Manifests section that:

* Generates IIIF Presentation API 3.0 manifests from the HMO graph
  (always written to disk — no credentials needed for this step).
* Optionally uploads them to the project Wikibase Cloud
  (``mhm-hmo.wikibase.cloud``) under the ``IIIF:`` namespace. The upload
  step requires bot credentials configured via the panel's
  ``Configure Credentials`` dialog (stored in OS keychain via
  ``SettingsManager``).

The Wikibase Cloud is a **separate trust boundary** from public
``wikidata.org``: Rule 25 (moratorium) and Rule 38 (four-stage uploader
guard) do not apply here. Every cloud edit is recorded under the
configured bot account and is reversible via page history.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from converter.wikibase.models import WikibaseEntityDraft
from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.dynamic_progress_bar import DynamicProgressBar
from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.glass_dialog import GlassDialog
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.settings.settings_manager import SettingsManager

_DEFAULT_WIKIBASE_URL = "https://mhm-hmo.wikibase.cloud"
_JSON_FILENAME = "wikibase_entities.json"
_QS_FILENAME = "wikibase_quickstatements.txt"
_REPORT_FILENAME = "wikibase_export_report.json"
_IIIF_DIRNAME = "iiif_manifests"
_IIIF_UPLOAD_REPORT = "iiif_upload_report.json"


@dataclass(frozen=True)
class _BuildResult:
    """Artifacts produced by the background HMO Wikibase export build."""

    entities: list[WikibaseEntityDraft]
    json_path: Path
    quickstatements_path: Path
    report_path: Path
    total_statements: int


class _BuildDraftWorker(QThread):
    """Build local HMO Wikibase drafts without blocking the GUI."""

    finished_export = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)
    substep = pyqtSignal(str)

    def __init__(
        self,
        ttl_path: Path,
        output_dir: Path,
        wikibase_url: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ttl_path = ttl_path
        self._output_dir = output_dir
        self._wikibase_url = wikibase_url

    def run(self) -> None:
        """Build draft entities and write all local export artifacts."""
        try:
            from converter.wikibase.hmo_exporter import HmoWikibaseExporter  # noqa: PLC0415
            from converter.wikibase.quickstatements_exporter import (  # noqa: PLC0415
                LocalQuickStatementsExporter,
            )

            self.progress.emit(0)
            self.substep.emit("Parsing HMO TTL graph")
            self.log_line.emit(f"Reading HMO RDF from {self._ttl_path}")

            exporter = HmoWikibaseExporter()
            entities = exporter.from_ttl(self._ttl_path)
            total_statements = sum(len(entity.statements) for entity in entities)
            self.progress.emit(45)

            self.substep.emit("Writing offline JSON draft")
            self._output_dir.mkdir(parents=True, exist_ok=True)
            json_path = self._output_dir / _JSON_FILENAME
            exporter.export_json_to_file(entities, json_path)
            self.progress.emit(65)

            self.substep.emit("Writing local QuickStatements draft")
            quickstatements_path = self._output_dir / _QS_FILENAME
            LocalQuickStatementsExporter().export_to_file(entities, quickstatements_path)
            self.progress.emit(85)

            self.substep.emit("Writing export report")
            report_path = self._output_dir / _REPORT_FILENAME
            _write_export_report(
                report_path=report_path,
                ttl_path=self._ttl_path,
                output_dir=self._output_dir,
                wikibase_url=self._wikibase_url,
                total_entities=len(entities),
                total_statements=total_statements,
                json_path=json_path,
                quickstatements_path=quickstatements_path,
            )
            self.progress.emit(100)

            self.finished_export.emit(
                _BuildResult(
                    entities=entities,
                    json_path=json_path,
                    quickstatements_path=quickstatements_path,
                    report_path=report_path,
                    total_statements=total_statements,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _ConnectionTestWorker(QThread):
    """Run a read-only Wikibase Cloud siteinfo check off the UI thread."""

    finished_test = pyqtSignal(object)

    def __init__(self, wikibase_url: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._wikibase_url = wikibase_url

    def run(self) -> None:
        """Fetch read-only Wikibase siteinfo and emit the result object."""
        from converter.wikibase.cloud_client import (  # noqa: PLC0415
            WikibaseCloudClient,
            WikibaseEndpointConfig,
        )

        result = WikibaseCloudClient(
            WikibaseEndpointConfig(base_url=self._wikibase_url),
            timeout=12.0,
        ).test_connection()
        self.finished_test.emit(result)


@dataclass(frozen=True)
class _IiifBuildResult:
    """Outputs of the IIIF manifest build worker."""

    manifest_paths: list[Path]
    total_canvases: int
    total_ranges: int
    total_annotations: int


@dataclass(frozen=True)
class _IiifUploadResult:
    """Per-manifest upload outcome, surfaced to the GUI table."""

    shelfmark: str
    page_url: str
    status: str
    message: str


class _IiifBuildWorker(QThread):
    """Generate IIIF Presentation 3.0 manifests from the HMO TTL graph.

    Always writes to ``<output_dir>/iiif_manifests/MS_<shelfmark>.json``.
    No network calls, no credentials required.
    """

    finished_build = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)
    substep = pyqtSignal(str)

    def __init__(
        self,
        ttl_path: Path,
        output_dir: Path,
        wikibase_url: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ttl_path = ttl_path
        self._output_dir = output_dir
        self._wikibase_url = wikibase_url

    def run(self) -> None:
        """Parse the TTL and write one manifest per manuscript."""
        try:
            from converter.wikidata.iiif_manifest_builder import (  # noqa: PLC0415
                IiifManifestBuilder,
            )
            from rdflib import Graph  # noqa: PLC0415

            self.progress.emit(0)
            self.substep.emit("Parsing HMO RDF for IIIF")
            self.log_line.emit(f"IIIF: reading {self._ttl_path}")

            graph = Graph()
            graph.parse(self._ttl_path)
            builder = IiifManifestBuilder(graph, base_url=self._wikibase_url)

            manifest_dir = self._output_dir / _IIIF_DIRNAME
            manifest_dir.mkdir(parents=True, exist_ok=True)
            self.substep.emit("Generating IIIF manifests")

            paths: list[Path] = []
            total_canvases = 0
            total_ranges = 0
            total_annotations = 0
            built: list[tuple[str, object, object]] = list(builder.build_all())
            n_total = max(1, len(built))
            for idx, (shelfmark, manifest, stats) in enumerate(built, start=1):
                manifest_path = manifest_dir / f"MS_{shelfmark}.json"
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                paths.append(manifest_path)
                total_canvases += int(getattr(stats, "canvas_count", 0))
                total_ranges += int(getattr(stats, "range_count", 0))
                total_annotations += int(getattr(stats, "annotation_count", 0))
                self.progress.emit(int(100 * idx / n_total))

            self.log_line.emit(
                f"IIIF: wrote {len(paths)} manifests "
                f"({total_canvases} canvases, {total_ranges} ranges, "
                f"{total_annotations} annotations) → {manifest_dir}"
            )
            self.finished_build.emit(
                _IiifBuildResult(
                    manifest_paths=paths,
                    total_canvases=total_canvases,
                    total_ranges=total_ranges,
                    total_annotations=total_annotations,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _IiifUploadWorker(QThread):
    """Upload locally-written IIIF manifests to ``mhm-hmo.wikibase.cloud``.

    Gated on bot credentials being present in :class:`SettingsManager`.
    Emits per-manifest status via the ``manifest_status`` signal so the
    panel can populate its upload-results table live.
    """

    finished_upload = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)
    substep = pyqtSignal(str)
    manifest_status = pyqtSignal(object)  # _IiifUploadResult

    def __init__(
        self,
        manifest_paths: list[Path],
        output_dir: Path,
        wikibase_url: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manifest_paths = manifest_paths
        self._output_dir = output_dir
        self._wikibase_url = wikibase_url

    def run(self) -> None:
        """Authenticate and upload one page per manifest."""
        try:
            from converter.wikibase.cloud_client import (  # noqa: PLC0415
                WikibaseCloudClient,
                WikibaseCloudWriter,
            )
            from converter.wikidata.iiif_manifest_builder import (  # noqa: PLC0415
                BuildStats,
            )
            from converter.wikidata.iiif_uploader import (  # noqa: PLC0415
                IiifManifestUploader,
            )

            settings = SettingsManager()
            credentials = settings.wikibase_cloud_credentials
            if credentials is None:
                self.failed.emit(
                    "Wikibase Cloud bot credentials not configured. "
                    "Click 'Configure Credentials' first."
                )
                return

            self.progress.emit(0)
            self.substep.emit("Authenticating with wikibase.cloud")
            writer = WikibaseCloudWriter(
                WikibaseCloudClient.config_for_mhm_hmo_cloud(),
                credentials,
            )
            uploader = IiifManifestUploader(writer, dry_run=False)

            self.substep.emit("Uploading IIIF manifests")
            report: list[dict[str, object]] = []
            n_total = max(1, len(self._manifest_paths))
            for idx, manifest_path in enumerate(self._manifest_paths, start=1):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                shelfmark = _shelfmark_from_path(manifest_path)
                stats = BuildStats(
                    canvas_count=len(manifest.get("items") or []),
                    range_count=len(manifest.get("structures") or []),
                    annotation_count=sum(
                        len(p.get("items") or [])
                        for p in (manifest.get("annotations") or [])
                    ),
                    seealso_count=len(manifest.get("seeAlso") or []),
                )
                upload = uploader.upload(shelfmark, manifest, stats)
                report.append(
                    {
                        "shelfmark": upload.shelfmark,
                        "page_url": upload.page_url,
                        "status": upload.status,
                        "message": upload.message,
                        "edit_id": upload.edit_id,
                        "new_revid": upload.new_revid,
                        "canvas_count": upload.canvas_count,
                        "range_count": upload.range_count,
                        "annotation_count": upload.annotation_count,
                    }
                )
                self.manifest_status.emit(
                    _IiifUploadResult(
                        shelfmark=upload.shelfmark,
                        page_url=upload.page_url,
                        status=upload.status,
                        message=upload.message,
                    )
                )
                self.progress.emit(int(100 * idx / n_total))

            report_path = self._output_dir / _IIIF_UPLOAD_REPORT
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            created = sum(1 for r in report if r["status"] == "created")
            updated = sum(1 for r in report if r["status"] == "updated")
            unchanged = sum(1 for r in report if r["status"] == "unchanged")
            failed_n = sum(1 for r in report if r["status"] == "failed")
            self.log_line.emit(
                f"IIIF upload: {created} created, {updated} updated, "
                f"{unchanged} unchanged, {failed_n} failed "
                f"(report: {report_path.name})"
            )
            self.finished_upload.emit(report)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def _shelfmark_from_path(path: Path) -> str:
    """Derive the NLI control number from ``MS_<cn>.json`` filename."""
    stem = path.stem  # e.g., "MS_990000123"
    return stem.removeprefix("MS_")


class _BotCredentialsDialog(GlassDialog):
    """Small modal to enter / clear Wikibase Cloud bot credentials.

    Per CLAUDE.md Rule 37 the dialog uses the glass backdrop. Per
    Rule 45 the password is stored via :class:`SettingsManager` (OS
    keychain on macOS / Credential Manager on Windows) and never
    written to disk in plaintext.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Wikibase Cloud Bot Credentials")
        self.setMinimumWidth(480)

        settings = SettingsManager()
        layout = QVBoxLayout(self.glass_content)

        info = QLabel(
            "<b>Project Wikibase — separate trust boundary from Wikidata.</b><br>"
            "Credentials are stored in your OS keychain (not on disk in plaintext).<br>"
            "Create a bot password at "
            "<a href='https://mhm-hmo.wikibase.cloud/wiki/Special:BotPasswords'>"
            "Special:BotPasswords</a>.<br>"
            "See CLAUDE.md Rule 45 for the safety model."
        )
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setStyleSheet(f"color: {theme.ui('text')};")
        layout.addWidget(info)

        self._username_edit = QLineEdit(settings.wikibase_cloud_bot_username)
        self._username_edit.setPlaceholderText("Username (e.g. AlexanderGoldbergIL)")
        layout.addWidget(QLabel("Username:"))
        layout.addWidget(self._username_edit)

        self._bot_name_edit = QLineEdit(settings.wikibase_cloud_bot_name)
        self._bot_name_edit.setPlaceholderText(
            "Bot password name (the part after @ in Special:BotPasswords)"
        )
        layout.addWidget(QLabel("Bot password name:"))
        layout.addWidget(self._bot_name_edit)

        self._password_edit = QLineEdit(settings.wikibase_cloud_bot_password)
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setPlaceholderText("(leave blank to keep current)")
        layout.addWidget(QLabel("Bot password:"))
        layout.addWidget(self._password_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._clear_btn = QPushButton("Clear stored credentials")
        self._clear_btn.clicked.connect(self._clear_credentials)
        buttons.addButton(self._clear_btn, QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.accepted.connect(self._save_credentials)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save_credentials(self) -> None:
        settings = SettingsManager()
        settings.wikibase_cloud_bot_username = self._username_edit.text().strip()
        settings.wikibase_cloud_bot_name = self._bot_name_edit.text().strip()
        new_password = self._password_edit.text()
        if new_password:  # only overwrite when user typed something
            settings.wikibase_cloud_bot_password = new_password
        self.accept()

    def _clear_credentials(self) -> None:
        settings = SettingsManager()
        settings.wikibase_cloud_bot_username = ""
        settings.wikibase_cloud_bot_name = ""
        settings.wikibase_cloud_bot_password = ""
        self._username_edit.setText("")
        self._bot_name_edit.setText("")
        self._password_edit.setText("")
        self.accept()


class HmoWikibasePanel(QWidget):
    """Panel for optional offline HMO Wikibase exports."""

    test_connection_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entities: list[WikibaseEntityDraft] = []
        self._worker: _BuildDraftWorker | None = None
        self._connection_worker: _ConnectionTestWorker | None = None
        self._iiif_build_worker: _IiifBuildWorker | None = None
        self._iiif_upload_worker: _IiifUploadWorker | None = None
        self._iiif_manifest_paths: list[Path] = []

        layout = QVBoxLayout(self)

        self._ttl_selector = FileSelector(
            "HMO RDF / TTL:",
            mode="open",
            filter="Turtle files (*.ttl);;RDF files (*.rdf *.nt *.jsonld);;All files (*)",
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._ttl_selector)
        layout.addWidget(self._output_selector)

        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("Wikibase URL:"))
        self._wikibase_url_edit = QLineEdit(_DEFAULT_WIKIBASE_URL)
        self._wikibase_url_edit.setPlaceholderText(_DEFAULT_WIKIBASE_URL)
        url_layout.addWidget(self._wikibase_url_edit, stretch=1)
        layout.addLayout(url_layout)

        button_layout = QHBoxLayout()
        self._build_btn = self._make_button("Build Draft")
        self._build_btn.clicked.connect(self._on_build_draft)
        button_layout.addWidget(self._build_btn)

        self._export_json_btn = self._make_button("Export JSON", "load")
        self._export_json_btn.clicked.connect(self._on_export_json)
        self._export_json_btn.setEnabled(False)
        button_layout.addWidget(self._export_json_btn)

        self._export_qs_btn = self._make_button("Export QuickStatements", "load")
        self._export_qs_btn.clicked.connect(self._on_export_quickstatements)
        self._export_qs_btn.setEnabled(False)
        button_layout.addWidget(self._export_qs_btn)

        self._test_connection_btn = self._make_button("Test Connection", "ghost")
        self._test_connection_btn.clicked.connect(self._on_test_connection)
        self._test_connection_btn.setToolTip(
            "Check the Wikibase Cloud API with a read-only siteinfo request."
        )
        button_layout.addWidget(self._test_connection_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        self._status_label = QLabel("Ready to build an offline HMO Wikibase draft.")
        self._status_label.setStyleSheet(f"color: {theme.ui('subtext')};")
        layout.addWidget(self._status_label)

        self._progress = DynamicProgressBar()
        layout.addWidget(self._progress)

        summary_layout = QHBoxLayout()
        self._entities_card, self._entities_value = self._summary_card("Total Entities", "0")
        self._statements_card, self._statements_value = self._summary_card("Statements", "0")
        summary_layout.addWidget(self._entities_card)
        summary_layout.addWidget(self._statements_card)
        summary_layout.addStretch()
        layout.addLayout(summary_layout)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Local ID", "Type", "Label", "#Statements", "Source URI"]
        )
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table, stretch=3)

        # ── IIIF Manifests section (Rule 45, Phase 3) ─────────────────
        layout.addWidget(self._build_iiif_section())

        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

    def _build_iiif_section(self) -> QWidget:
        """Build the IIIF Manifests section.

        Always-on Generate button (writes manifests locally); Upload button
        gated on bot credentials being configured via the credentials dialog.
        """
        section = QFrame()
        section.setStyleSheet(
            "QFrame {"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_LG}px;"
            f" background: {theme.ui('panel_bg')};"
            f" padding: {theme.SPACE_SM}px;"
            "}"
        )
        section_layout = QVBoxLayout(section)

        title = QLabel("IIIF Manifests (Phase 3 / Rule 45)")
        title.setStyleSheet(
            f"color: {theme.ui('text')};"
            f" font-size: {theme.FONT_LG}px;"
            f" font-weight: {theme.WEIGHT_SEMIBOLD};"
        )
        section_layout.addWidget(title)

        info = QLabel(
            "Generate IIIF Presentation 3.0 manifests from the HMO graph "
            "(one per manuscript, with Codicological_Unit Ranges and "
            "ScribalIntervention / Colophon AnnotationCollections). "
            "Upload goes to <b>mhm-hmo.wikibase.cloud</b> — a separate "
            "trust boundary from Wikidata; Rule 25 / 38 do not apply here. "
            "Every cloud edit is recorded under your bot account."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme.ui('subtext')};")
        section_layout.addWidget(info)

        button_row = QHBoxLayout()
        self._iiif_build_btn = self._make_button("Generate IIIF Manifests")
        self._iiif_build_btn.clicked.connect(self._on_iiif_build)
        button_row.addWidget(self._iiif_build_btn)

        self._iiif_configure_btn = self._make_button("Configure Credentials", "ghost")
        self._iiif_configure_btn.clicked.connect(self._on_configure_credentials)
        button_row.addWidget(self._iiif_configure_btn)

        self._iiif_upload_btn = self._make_button("Upload to wikibase.cloud", "warning")
        self._iiif_upload_btn.setEnabled(False)
        self._iiif_upload_btn.clicked.connect(self._on_iiif_upload)
        button_row.addWidget(self._iiif_upload_btn)

        self._iiif_open_folder_btn = self._make_button("Open Manifest Folder", "ghost")
        self._iiif_open_folder_btn.setEnabled(False)
        self._iiif_open_folder_btn.clicked.connect(self._on_open_manifest_folder)
        button_row.addWidget(self._iiif_open_folder_btn)
        button_row.addStretch()
        section_layout.addLayout(button_row)

        self._iiif_status_label = QLabel(self._iiif_status_text())
        self._iiif_status_label.setStyleSheet(f"color: {theme.ui('subtext')};")
        section_layout.addWidget(self._iiif_status_label)

        self._iiif_upload_table = QTableWidget(0, 3)
        self._iiif_upload_table.setHorizontalHeaderLabels(
            ["Shelfmark", "Status", "Page URL"]
        )
        iiif_header = self._iiif_upload_table.horizontalHeader()
        if iiif_header is not None:
            iiif_header.setStretchLastSection(True)
            iiif_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            iiif_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._iiif_upload_table.setVisible(False)
        section_layout.addWidget(self._iiif_upload_table)

        return section

    # ── IIIF section handlers ───────────────────────────────────────────

    def _iiif_status_text(self) -> str:
        """Status banner: credentials configured or not."""
        creds = SettingsManager().wikibase_cloud_credentials
        if creds is None:
            return (
                "No Wikibase Cloud credentials configured. "
                "Generate writes manifests locally; Upload is disabled."
            )
        username = SettingsManager().wikibase_cloud_bot_username
        bot = SettingsManager().wikibase_cloud_bot_name
        return f"Bot credentials configured ({username}@{bot}). Ready to upload."

    def _refresh_iiif_status(self) -> None:
        self._iiif_status_label.setText(self._iiif_status_text())
        self._iiif_upload_btn.setEnabled(
            bool(self._iiif_manifest_paths)
            and SettingsManager().wikibase_cloud_credentials is not None
        )

    def _on_iiif_build(self) -> None:
        ttl_path = self._ttl_selector.path
        if ttl_path is None:
            self._set_status(
                "Select an HMO RDF / TTL file before generating IIIF manifests.",
                is_error=True,
            )
            return
        output_dir = self._resolved_output_dir()

        self._iiif_build_btn.setEnabled(False)
        self._progress.reset()
        self._progress.set_total(100)
        self._set_status("Generating IIIF manifests...")
        self._log_viewer.append_line("Starting IIIF manifest generation.")

        self._iiif_build_worker = _IiifBuildWorker(
            ttl_path=ttl_path,
            output_dir=output_dir,
            wikibase_url=self._wikibase_url(),
            parent=self,
        )
        self._iiif_build_worker.progress.connect(self._progress.set_progress)
        self._iiif_build_worker.substep.connect(self._progress.set_substep)
        self._iiif_build_worker.log_line.connect(self._log_viewer.append_line)
        self._iiif_build_worker.finished_build.connect(self._on_iiif_build_finished)
        self._iiif_build_worker.failed.connect(self._on_iiif_build_failed)
        self._iiif_build_worker.start()

    def _on_iiif_build_finished(self, result_object: object) -> None:
        if not isinstance(result_object, _IiifBuildResult):
            self._on_iiif_build_failed("Unexpected IIIF build worker result.")
            return
        self._iiif_manifest_paths = list(result_object.manifest_paths)
        self._iiif_build_btn.setEnabled(True)
        self._iiif_open_folder_btn.setEnabled(True)
        self._progress.finish("IIIF manifests generated", success=True)
        self._set_status(
            f"Generated {len(self._iiif_manifest_paths)} IIIF manifests "
            f"({result_object.total_canvases} canvases, "
            f"{result_object.total_ranges} ranges, "
            f"{result_object.total_annotations} annotations)."
        )
        self._refresh_iiif_status()
        self._iiif_build_worker = None

    def _on_iiif_build_failed(self, message: str) -> None:
        self._iiif_build_btn.setEnabled(True)
        self._progress.finish("IIIF generation failed", success=False)
        self._set_status(f"IIIF generation failed: {message}", is_error=True)
        self._log_viewer.append_line(f"IIIF error: {message}")
        self._iiif_build_worker = None

    def _on_configure_credentials(self) -> None:
        dialog = _BotCredentialsDialog(parent=self)
        dialog.exec()
        self._refresh_iiif_status()
        creds_set = SettingsManager().wikibase_cloud_credentials is not None
        self._log_viewer.append_line(
            "Wikibase Cloud credentials "
            f"{'configured' if creds_set else 'cleared'}."
        )

    def _on_iiif_upload(self) -> None:
        if not self._iiif_manifest_paths:
            self._set_status(
                "Generate IIIF manifests before uploading.", is_error=True,
            )
            return
        if SettingsManager().wikibase_cloud_credentials is None:
            self._set_status(
                "Configure Wikibase Cloud bot credentials before uploading.",
                is_error=True,
            )
            return
        output_dir = self._resolved_output_dir()

        self._iiif_upload_btn.setEnabled(False)
        self._iiif_upload_table.setRowCount(0)
        self._iiif_upload_table.setVisible(True)
        self._progress.reset()
        self._progress.set_total(100)
        self._set_status("Uploading IIIF manifests to wikibase.cloud...")
        self._log_viewer.append_line(
            f"Uploading {len(self._iiif_manifest_paths)} manifests to "
            "mhm-hmo.wikibase.cloud (Rule 45)."
        )

        self._iiif_upload_worker = _IiifUploadWorker(
            manifest_paths=list(self._iiif_manifest_paths),
            output_dir=output_dir,
            wikibase_url=self._wikibase_url(),
            parent=self,
        )
        self._iiif_upload_worker.progress.connect(self._progress.set_progress)
        self._iiif_upload_worker.substep.connect(self._progress.set_substep)
        self._iiif_upload_worker.log_line.connect(self._log_viewer.append_line)
        self._iiif_upload_worker.manifest_status.connect(self._on_iiif_status_row)
        self._iiif_upload_worker.finished_upload.connect(self._on_iiif_upload_finished)
        self._iiif_upload_worker.failed.connect(self._on_iiif_upload_failed)
        self._iiif_upload_worker.start()

    def _on_iiif_status_row(self, row_object: object) -> None:
        if not isinstance(row_object, _IiifUploadResult):
            return
        row = self._iiif_upload_table.rowCount()
        self._iiif_upload_table.insertRow(row)
        self._iiif_upload_table.setItem(row, 0, QTableWidgetItem(row_object.shelfmark))
        self._iiif_upload_table.setItem(row, 1, QTableWidgetItem(row_object.status))
        self._iiif_upload_table.setItem(row, 2, QTableWidgetItem(row_object.page_url))

    def _on_iiif_upload_finished(self, report_object: object) -> None:
        if not isinstance(report_object, list):
            self._on_iiif_upload_failed("Unexpected IIIF upload worker result.")
            return
        created = sum(1 for r in report_object if r.get("status") == "created")
        updated = sum(1 for r in report_object if r.get("status") == "updated")
        unchanged = sum(1 for r in report_object if r.get("status") == "unchanged")
        failed = sum(1 for r in report_object if r.get("status") == "failed")
        self._iiif_upload_btn.setEnabled(True)
        self._progress.finish("IIIF upload complete", success=failed == 0)
        self._set_status(
            f"IIIF upload: {created} created, {updated} updated, "
            f"{unchanged} unchanged, {failed} failed."
        )
        self._iiif_upload_worker = None

    def _on_iiif_upload_failed(self, message: str) -> None:
        self._iiif_upload_btn.setEnabled(True)
        self._progress.finish("IIIF upload failed", success=False)
        self._set_status(f"IIIF upload failed: {message}", is_error=True)
        self._log_viewer.append_line(f"IIIF upload error: {message}")
        self._iiif_upload_worker = None

    def _on_open_manifest_folder(self) -> None:
        if not self._iiif_manifest_paths:
            return
        folder = self._iiif_manifest_paths[0].parent
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=False)  # noqa: S603, S607
            elif sys.platform.startswith("linux"):
                subprocess.run(["xdg-open", str(folder)], check=False)  # noqa: S603, S607
            elif sys.platform == "win32":
                subprocess.run(["explorer", str(folder)], check=False)  # noqa: S603, S607
        except Exception as exc:  # noqa: BLE001
            self._log_viewer.append_line(f"Failed to open folder: {exc}")

    @property
    def log_viewer(self) -> LogViewer:
        """Return the embedded log viewer."""
        return self._log_viewer

    @property
    def stage_progress(self) -> DynamicProgressBar:
        """Return the embedded progress bar."""
        return self._progress

    def set_ttl_path(self, path: Path | str) -> None:
        """Set the source HMO RDF / TTL path for main-window integration."""
        self._ttl_selector.path = Path(path)

    def set_output_dir(self, path: Path | str) -> None:
        """Set the export output directory for main-window integration."""
        self._output_selector.path = Path(path)

    def current_ttl_path(self) -> Path | None:
        """Return the currently selected TTL path, if any."""
        return self._ttl_selector.path

    def _make_button(self, label: str, variant: str = "primary") -> QPushButton:
        button = QPushButton(label)
        button.setStyleSheet(theme.button_style(variant))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _summary_card(self, title: str, value: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            "QFrame {"
            f" border: 1px solid {theme.ui('border')};"
            f" border-radius: {theme.RADIUS_LG}px;"
            f" background: {theme.ui('panel_bg')};"
            "}"
        )
        layout = QVBoxLayout(card)
        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"color: {theme.ui('subtext')}; font-size: {theme.FONT_XS}px;"
        )
        value_label = QLabel(value)
        value_label.setStyleSheet(
            f"color: {theme.ui('text')};"
            f" font-size: {theme.FONT_2XL}px;"
            f" font-weight: {theme.WEIGHT_SEMIBOLD};"
        )
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return card, value_label

    def _on_build_draft(self) -> None:
        ttl_path = self._ttl_selector.path
        if ttl_path is None:
            self._set_status("Select an HMO RDF / TTL file before building.", is_error=True)
            self._log_viewer.append_line("Error: no HMO RDF / TTL file selected.")
            return
        output_dir = self._output_selector.path or ttl_path.parent
        self._output_selector.path = output_dir

        self._set_busy(True)
        self._progress.reset()
        self._progress.set_total(100)
        self._set_status("Building offline HMO Wikibase draft...")
        self._log_viewer.append_line("Starting HMO Wikibase draft export.")

        self._worker = _BuildDraftWorker(
            ttl_path=ttl_path,
            output_dir=output_dir,
            wikibase_url=self._wikibase_url(),
            parent=self,
        )
        self._worker.progress.connect(self._progress.set_progress)
        self._worker.substep.connect(self._progress.set_substep)
        self._worker.log_line.connect(self._log_viewer.append_line)
        self._worker.finished_export.connect(self._on_build_finished)
        self._worker.failed.connect(self._on_build_failed)
        self._worker.start()

    def _on_build_finished(self, result_object: object) -> None:
        if not isinstance(result_object, _BuildResult):
            self._on_build_failed("Unexpected HMO Wikibase worker result.")
            return
        self._entities = result_object.entities
        self._populate_table(self._entities)
        self._entities_value.setText(str(len(self._entities)))
        self._statements_value.setText(str(result_object.total_statements))
        self._export_json_btn.setEnabled(True)
        self._export_qs_btn.setEnabled(True)
        self._set_busy(False)
        self._progress.finish("HMO Wikibase draft built", success=True)
        self._set_status(f"Built {len(self._entities)} entities for offline export.")
        self._log_viewer.append_line(f"Wrote {result_object.json_path}")
        self._log_viewer.append_line(f"Wrote {result_object.quickstatements_path}")
        self._log_viewer.append_line(f"Wrote {result_object.report_path}")
        self._worker = None

    def _on_build_failed(self, message: str) -> None:
        self._set_busy(False)
        self._progress.finish("HMO Wikibase draft failed", success=False)
        self._set_status(f"Build failed: {message}", is_error=True)
        self._log_viewer.append_line(f"Error: {message}")
        self._worker = None

    def _on_export_json(self) -> None:
        if not self._entities:
            self._log_viewer.append_line("No draft entities available. Build Draft first.")
            return
        output_dir = self._resolved_output_dir()
        from converter.wikibase.hmo_exporter import HmoWikibaseExporter  # noqa: PLC0415

        path = HmoWikibaseExporter().export_json_to_file(
            self._entities,
            output_dir / _JSON_FILENAME,
        )
        self._log_viewer.append_line(f"Wrote {path}")
        self._set_status(f"Exported JSON to {path.name}.")

    def _on_export_quickstatements(self) -> None:
        if not self._entities:
            self._log_viewer.append_line("No draft entities available. Build Draft first.")
            return
        output_dir = self._resolved_output_dir()
        from converter.wikibase.quickstatements_exporter import (  # noqa: PLC0415
            LocalQuickStatementsExporter,
        )

        path = LocalQuickStatementsExporter().export_to_file(
            self._entities,
            output_dir / _QS_FILENAME,
        )
        self._log_viewer.append_line(f"Wrote {path}")
        self._set_status(f"Exported QuickStatements to {path.name}.")

    def _on_test_connection(self) -> None:
        url = self._wikibase_url()
        self.test_connection_requested.emit(url)
        self._test_connection_btn.setEnabled(False)
        self._set_status(f"Testing read-only connection to {url}...")
        self._log_viewer.append_line(f"Testing Wikibase API connection: {url}")

        self._connection_worker = _ConnectionTestWorker(url, parent=self)
        self._connection_worker.finished_test.connect(self._on_connection_test_finished)
        self._connection_worker.start()

    def _on_connection_test_finished(self, result_object: object) -> None:
        self._test_connection_btn.setEnabled(True)
        self._connection_worker = None

        ok = bool(getattr(result_object, "ok", False))
        message = str(getattr(result_object, "message", "Unknown connection result"))
        api_url = str(getattr(result_object, "api_url", ""))
        site_name = str(getattr(result_object, "site_name", ""))
        generator = str(getattr(result_object, "generator", ""))

        if ok:
            details = f"{site_name} ({generator})" if generator else site_name
            self._set_status(f"Connection OK: {details}")
            self._log_viewer.append_line(f"Connection OK: {details}")
        else:
            self._set_status(f"Connection failed: {message}", is_error=True)
            self._log_viewer.append_line(f"Connection failed: {message}")
        if api_url:
            self._log_viewer.append_line(f"API endpoint: {api_url}")

    def _populate_table(self, entities: list[WikibaseEntityDraft]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for entity in entities:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(entity.local_id))
            self._table.setItem(row, 1, QTableWidgetItem(entity.entity_type))
            self._table.setItem(row, 2, QTableWidgetItem(_preferred_label(entity)))
            self._table.setItem(row, 3, QTableWidgetItem(str(len(entity.statements))))
            self._table.setItem(row, 4, QTableWidgetItem(entity.source_uri))
        self._table.setSortingEnabled(True)

    def _set_busy(self, busy: bool) -> None:
        self._build_btn.setEnabled(not busy)
        self._export_json_btn.setEnabled((not busy) and bool(self._entities))
        self._export_qs_btn.setEnabled((not busy) and bool(self._entities))

    def _set_status(self, message: str, *, is_error: bool = False) -> None:
        self._status_label.setText(message)
        color = theme.severity("violation").text if is_error else theme.ui("subtext")
        self._status_label.setStyleSheet(f"color: {color};")

    def _wikibase_url(self) -> str:
        return self._wikibase_url_edit.text().strip() or _DEFAULT_WIKIBASE_URL

    def _resolved_output_dir(self) -> Path:
        output_dir = self._output_selector.path
        if output_dir is None:
            ttl_path = self._ttl_selector.path
            output_dir = ttl_path.parent if ttl_path is not None else Path.cwd()
            self._output_selector.path = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir


def _preferred_label(entity: WikibaseEntityDraft) -> str:
    """Return the best display label for a draft entity."""
    return (
        entity.labels.get("he")
        or entity.labels.get("en")
        or next(iter(entity.labels.values()), entity.local_id)
    )


def _write_export_report(
    *,
    report_path: Path,
    ttl_path: Path,
    output_dir: Path,
    wikibase_url: str,
    total_entities: int,
    total_statements: int,
    json_path: Path,
    quickstatements_path: Path,
) -> None:
    """Write a small deterministic report for the offline export run."""
    report: dict[str, object] = {
        "mode": "offline_export_only",
        "upload_performed": False,
        "wikibase_url": wikibase_url,
        "ttl_path": str(ttl_path),
        "output_dir": str(output_dir),
        "total_entities": total_entities,
        "total_statements": total_statements,
        "artifacts": {
            "json": str(json_path),
            "quickstatements": str(quickstatements_path),
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _wikibase_backend_available() -> bool:
    """Return whether the read-only connection-test backend is importable."""
    return importlib.util.find_spec("converter.wikibase.cloud_client") is not None
