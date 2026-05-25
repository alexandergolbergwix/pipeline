"""Settings → Credentials… — the unified API-key entry surface (Rule 50).

Holds the three secret-shaped values the pipeline depends on:

1. **Gemini API key** — used by the bundled eval-agent's
   ``Verify with AI agent`` step.
2. **Wikidata token** — used by Stage 6 Wikidata upload.
3. **MHM Wikibase Cloud bot password** (plus username + bot-password
   name) — used by Stage 6.5 IIIF manifest upload.

**No read-back UX** (the user's directive on 2026-05-25): when a
value is already stored, the input field is shown EMPTY with a
placeholder ``"stored — type to replace"``. The user can:

* Type a new value → on Save, replaces the stored value.
* Tick "Show" while typing → reveals what they're entering (never
  what's already stored).
* Click "Clear" → deletes the stored value entirely.
* Leave the field empty + click Save → the existing stored value is
  preserved untouched.

The dialog never *reads* the stored value back into the input — it
only consults :meth:`CredentialStore.has` to decide which placeholder
to render.

Mirrors :class:`mhm_pipeline.gui.widgets.glass_dialog.GlassDialog` per
CLAUDE.md Rule 37 so the liquid-glass backdrop applies.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui import theme
from mhm_pipeline.gui.widgets.glass_dialog import GlassDialog
from mhm_pipeline.settings.credential_store import (
    GEMINI_API_KEY,
    WIKIBASE_CLOUD_BOT_PASSWORD,
    WIKIDATA_TOKEN,
)
from mhm_pipeline.settings.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

_STORED_PLACEHOLDER = "stored — type to replace"


class CredentialsDialog(GlassDialog):
    """Unified credentials entry. Constructed via ``CredentialsDialog(settings, parent)``.

    Emits :pyattr:`saved` after a successful save, carrying the
    keyring ids that changed (so listeners can refresh their views,
    e.g. the Verify-with-AI-agent button updating its enabled state).
    """

    saved = pyqtSignal(set)  # set[str] — credential ids that changed

    def __init__(
        self,
        settings: SettingsManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._store = settings.credentials_backend
        self.setWindowTitle("Credentials")
        self.setModal(True)
        self.setMinimumSize(560, 640)

        # Per-credential widgets — populated in _build_ui().
        self._gemini_input: QLineEdit | None = None
        self._gemini_show: QPushButton | None = None
        self._gemini_clear: QPushButton | None = None
        self._wikidata_input: QLineEdit | None = None
        self._wikidata_show: QPushButton | None = None
        self._wikidata_clear: QPushButton | None = None
        self._wb_username: QLineEdit | None = None
        self._wb_botname: QLineEdit | None = None
        self._wb_password: QLineEdit | None = None
        self._wb_password_show: QPushButton | None = None
        self._wb_password_clear: QPushButton | None = None

        self._build_ui()
        self._refresh_placeholders()

    # ── UI construction ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self.glass_content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        title = QLabel("<b>Credentials</b>")
        title.setStyleSheet(f"color:{theme.ui('text')}; font-size:{theme.FONT_LG}px;")
        outer.addWidget(title)

        subtitle = QLabel(
            "API tokens are stored encrypted in the OS keychain. "
            "The stored value is never displayed back — type a new value to replace, "
            "or click <b>Clear</b> to remove."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;")
        outer.addWidget(subtitle)

        outer.addWidget(self._build_gemini_section())
        outer.addWidget(_separator())
        outer.addWidget(self._build_wikidata_section())
        outer.addWidget(_separator())
        outer.addWidget(self._build_wikibase_section())

        outer.addStretch(1)

        # Footer: Help · Cancel · Save credentials
        buttons = QDialogButtonBox()
        help_btn = buttons.addButton("Help", QDialogButtonBox.ButtonRole.HelpRole)
        cancel_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        save_btn = buttons.addButton("Save credentials", QDialogButtonBox.ButtonRole.AcceptRole)
        if save_btn is not None:
            save_btn.setDefault(True)
            save_btn.clicked.connect(self._on_save)
        if cancel_btn is not None:
            cancel_btn.clicked.connect(self.reject)
        if help_btn is not None:
            help_btn.clicked.connect(self._on_help)
        outer.addWidget(buttons)

    def _build_gemini_section(self) -> QWidget:
        section = QFrame()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_XS)

        header = QLabel("<b>Gemini API key</b>")
        header.setStyleSheet(f"color:{theme.ui('text')};")
        layout.addWidget(header)

        hint = QLabel(
            "Used by the AI agent verification step. "
            "Free tier available at <a href='https://aistudio.google.com/app/apikey'>"
            "aistudio.google.com/app/apikey</a>."
        )
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;")
        layout.addWidget(hint)

        self._gemini_input = QLineEdit()
        self._gemini_input.setEchoMode(QLineEdit.EchoMode.Password)
        row = QHBoxLayout()
        row.addWidget(self._gemini_input, 1)
        self._gemini_show = _toggle_button("Show", self._gemini_input)
        row.addWidget(self._gemini_show)
        self._gemini_clear = QPushButton("Clear")
        self._gemini_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gemini_clear.clicked.connect(
            lambda: self._on_clear(GEMINI_API_KEY)
        )
        row.addWidget(self._gemini_clear)
        layout.addLayout(row)
        return section

    def _build_wikidata_section(self) -> QWidget:
        section = QFrame()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_XS)

        header = QLabel("<b>Wikidata token</b>")
        header.setStyleSheet(f"color:{theme.ui('text')};")
        layout.addWidget(header)

        hint = QLabel(
            "Used by Stage 6 Wikidata upload. Accepted formats: bot password "
            "(<code>User@Bot:password</code>), OAuth 2.0 owner-only consumer "
            "(<code>key|secret</code>), OAuth 1.0a (<code>key|secret|token|secret</code>), "
            "or JWT bearer (<code>eyJ…</code>). "
            "Set up at <a href='https://www.wikidata.org/wiki/Special:BotPasswords'>"
            "Special:BotPasswords</a>."
        )
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setStyleSheet(f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;")
        layout.addWidget(hint)

        self._wikidata_input = QLineEdit()
        self._wikidata_input.setEchoMode(QLineEdit.EchoMode.Password)
        row = QHBoxLayout()
        row.addWidget(self._wikidata_input, 1)
        self._wikidata_show = _toggle_button("Show", self._wikidata_input)
        row.addWidget(self._wikidata_show)
        self._wikidata_clear = QPushButton("Clear")
        self._wikidata_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wikidata_clear.clicked.connect(
            lambda: self._on_clear(WIKIDATA_TOKEN)
        )
        row.addWidget(self._wikidata_clear)
        layout.addLayout(row)
        return section

    def _build_wikibase_section(self) -> QWidget:
        section = QFrame()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_XS)

        header = QLabel("<b>MHM Wikibase Cloud</b>")
        header.setStyleSheet(f"color:{theme.ui('text')};")
        layout.addWidget(header)

        hint = QLabel(
            "Used by Stage 6.5 IIIF manifest upload. Separate trust boundary "
            "from Wikidata (CLAUDE.md Rule 45). Set up at "
            "<a href='https://mhm-hmo.wikibase.cloud/wiki/Special:BotPasswords'>"
            "mhm-hmo.wikibase.cloud Special:BotPasswords</a>."
        )
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setStyleSheet(f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;")
        layout.addWidget(hint)

        grid = QGridLayout()
        grid.setContentsMargins(0, theme.SPACE_XS, 0, 0)
        grid.setHorizontalSpacing(theme.SPACE_SM)
        grid.setVerticalSpacing(theme.SPACE_XS)

        grid.addWidget(QLabel("Username:"), 0, 0)
        self._wb_username = QLineEdit()
        self._wb_username.setText(self._settings.wikibase_cloud_bot_username)
        grid.addWidget(self._wb_username, 0, 1, 1, 2)

        grid.addWidget(QLabel("Bot password name:"), 1, 0)
        self._wb_botname = QLineEdit()
        self._wb_botname.setText(self._settings.wikibase_cloud_bot_name)
        self._wb_botname.setPlaceholderText("the part after @ in Special:BotPasswords")
        grid.addWidget(self._wb_botname, 1, 1, 1, 2)

        grid.addWidget(QLabel("Bot password:"), 2, 0)
        self._wb_password = QLineEdit()
        self._wb_password.setEchoMode(QLineEdit.EchoMode.Password)
        grid.addWidget(self._wb_password, 2, 1)
        self._wb_password_show = _toggle_button("Show", self._wb_password)
        grid.addWidget(self._wb_password_show, 2, 2)

        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        self._wb_password_clear = QPushButton("Clear bot password")
        self._wb_password_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wb_password_clear.clicked.connect(
            lambda: self._on_clear(WIKIBASE_CLOUD_BOT_PASSWORD)
        )
        clear_row.addWidget(self._wb_password_clear)
        grid.addLayout(clear_row, 3, 0, 1, 3)

        layout.addLayout(grid)
        return section

    # ── State sync ──────────────────────────────────────────────────

    def _refresh_placeholders(self) -> None:
        """Populate each masked input's placeholder + Clear button
        enabled state based on what's stored. The input itself stays
        empty — we never read the stored value back."""
        inputs_keys: list[tuple[QLineEdit | None, QPushButton | None, str, str]] = [
            (
                self._gemini_input,
                self._gemini_clear,
                GEMINI_API_KEY,
                "AIza…",
            ),
            (
                self._wikidata_input,
                self._wikidata_clear,
                WIKIDATA_TOKEN,
                "User@Bot:password · eyJ… · key|secret",
            ),
            (
                self._wb_password,
                self._wb_password_clear,
                WIKIBASE_CLOUD_BOT_PASSWORD,
                "long hex string from MediaWiki",
            ),
        ]
        for input_widget, clear_btn, key, empty_placeholder in inputs_keys:
            if input_widget is None:
                continue
            has = self._store.has(key)
            input_widget.setPlaceholderText(
                _STORED_PLACEHOLDER if has else empty_placeholder
            )
            if clear_btn is not None:
                clear_btn.setEnabled(has)

    # ── Slots ───────────────────────────────────────────────────────

    def _on_clear(self, key: str) -> None:
        """Delete the stored credential immediately (no undo)."""
        self._store.delete(key)
        self._refresh_placeholders()

    def _on_save(self) -> None:
        changed: set[str] = set()

        # Non-secret Wikibase fields (username + bot name): direct
        # SettingsManager properties. Empty string IS a valid value
        # here (you can clear a username) so no preserve-existing logic.
        new_username = (self._wb_username.text() if self._wb_username else "").strip()
        new_botname = (self._wb_botname.text() if self._wb_botname else "").strip()
        if new_username != self._settings.wikibase_cloud_bot_username:
            self._settings.wikibase_cloud_bot_username = new_username
            changed.add("wikibase_cloud_bot_username")
        if new_botname != self._settings.wikibase_cloud_bot_name:
            self._settings.wikibase_cloud_bot_name = new_botname
            changed.add("wikibase_cloud_bot_name")

        # Secrets — empty input means "keep existing stored value".
        for input_widget, key in [
            (self._gemini_input, GEMINI_API_KEY),
            (self._wikidata_input, WIKIDATA_TOKEN),
            (self._wb_password, WIKIBASE_CLOUD_BOT_PASSWORD),
        ]:
            if input_widget is None:
                continue
            typed = input_widget.text()
            if not typed:
                continue
            try:
                self._store.set(key, typed)
                changed.add(key)
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not store credential %s: %s", key, exc)

        self.saved.emit(changed)
        self.accept()

    def _on_help(self) -> None:
        """Open the Help → API Credentials topic in the help browser
        (or fall back to the upstream URL if the help browser is not
        available in this context)."""
        try:
            from mhm_pipeline.gui.widgets.help_browser import (  # noqa: PLC0415
                open_help,
            )

            open_help(topic="credentials", parent=self)
            return
        except Exception:
            QDesktopServices.openUrl(  # type: ignore[arg-type]
                "https://aistudio.google.com/app/apikey",
            )


# ── Internal helpers ────────────────────────────────────────────────


def _separator() -> QFrame:
    """A faint horizontal divider between credential sections."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color:{theme.ui('border')}; background:{theme.ui('border')};")
    line.setFixedHeight(1)
    return line


def _toggle_button(label: str, field: QLineEdit) -> QPushButton:
    """Build a Show/Hide toggle that flips the QLineEdit's echo mode.

    Reveals only what the user is TYPING — never the stored value
    (because the dialog never loads stored secrets into the input).
    """
    btn = QPushButton(label)
    btn.setCheckable(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _on_toggle(checked: bool) -> None:
        field.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        btn.setText("Hide" if checked else "Show")

    btn.toggled.connect(_on_toggle)
    return btn


__all__ = ["CredentialsDialog"]
