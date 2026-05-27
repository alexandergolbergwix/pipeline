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

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
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

# Curated suggestions for the AI-verification model combos. The combos are
# editable so a user can type any Gemini model id; these are convenient
# pre-filled choices. Every entry below is a verified-valid id that resolves
# on the v1beta generateContent endpoint (checked 2026-05-27) — DO NOT add a
# bare "gemini-3-pro" / "gemini-3-flash" (those 404; the real ids carry the
# "-preview" suffix). The "Refresh from API" button repopulates this list
# live from the user's own key so it always reflects what is actually
# callable.
_MODEL_SUGGESTIONS = [
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
]

_LIST_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"


def fetch_available_gemini_models(api_key: str, timeout: float = 6.0) -> list[str]:
    """Return the Gemini model ids callable with *api_key*, newest first.

    Queries the live ListModels endpoint and keeps only models that
    support ``generateContent`` (so embedding / TTS / image-only models
    don't pollute the judge combos). Never raises — any failure (no key,
    network error, bad JSON) returns an empty list so the caller falls
    back to the curated :data:`_MODEL_SUGGESTIONS`.
    """
    key = (api_key or "").strip()
    if not key:
        return []
    req = urllib.request.Request(  # noqa: S310 — fixed https host
        f"{_LIST_MODELS_URL}&key={urllib.parse.quote(key)}",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        logger.warning("ListModels fetch failed: %s", exc)
        return []
    out: list[str] = []
    for model in data.get("models", []):
        if "generateContent" not in (model.get("supportedGenerationMethods") or []):
            continue
        name = str(model.get("name", "")).split("/")[-1]
        if name.startswith("gemini-"):
            out.append(name)
    # Newest families first (gemini-3.5 > gemini-3.1 > gemini-3 > gemini-2.5),
    # then lexicographic within a family for stable ordering.
    return sorted(set(out), key=lambda m: (m.split("-")[:2], m), reverse=True)


class CredentialsDialog(GlassDialog):
    """Unified credentials entry. Constructed via ``CredentialsDialog(settings, parent)``.

    Emits :pyattr:`saved` after a successful save, carrying the
    keyring ids that changed (so listeners can refresh their views,
    e.g. the Verify-with-AI-agent button updating its enabled state).
    """

    saved = pyqtSignal(set)  # set[str] — credential ids that changed
    _models_fetched = pyqtSignal(list)  # list[str] — live model ids (internal)

    def __init__(
        self,
        settings: SettingsManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._store = settings.credentials_backend
        self.setWindowTitle("Settings & Credentials")
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
        # AI-verification model combos (non-secret — plain QSettings).
        self._tier_model_combo: QComboBox | None = None
        self._escalate_model_combo: QComboBox | None = None
        self._refresh_models_btn: QPushButton | None = None
        self._models_status: QLabel | None = None

        self._build_ui()
        self._refresh_placeholders()
        self._models_fetched.connect(self._apply_fetched_models)

    # ── UI construction ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self.glass_content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG,
        )
        outer.setSpacing(theme.SPACE_MD)

        title = QLabel("<b>Settings &amp; Credentials</b>")
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
        outer.addWidget(self._build_ai_models_section())
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

    def _build_ai_models_section(self) -> QWidget:
        section = QFrame()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_XS)

        header = QLabel("<b>AI verification models</b>")
        header.setStyleSheet(f"color:{theme.ui('text')};")
        layout.addWidget(header)

        hint = QLabel(
            "Gemini models the AI agent verification step uses. The cheap "
            "tier-1 model checks every prediction; hard cases escalate to the "
            "stronger model. Pick a suggestion or type any Gemini model id."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;")
        layout.addWidget(hint)

        grid = QGridLayout()
        grid.setContentsMargins(0, theme.SPACE_XS, 0, 0)
        grid.setHorizontalSpacing(theme.SPACE_SM)
        grid.setVerticalSpacing(theme.SPACE_XS)

        tier_label = QLabel("Tier-1 model (cheap pass):")
        tier_label.setStyleSheet(f"color:{theme.ui('subtext')};")
        grid.addWidget(tier_label, 0, 0)
        self._tier_model_combo = self._build_model_combo(
            self._settings.eval_agent_tier_model
        )
        grid.addWidget(self._tier_model_combo, 0, 1)

        escalate_label = QLabel("Escalation model (hard cases):")
        escalate_label.setStyleSheet(f"color:{theme.ui('subtext')};")
        grid.addWidget(escalate_label, 1, 0)
        self._escalate_model_combo = self._build_model_combo(
            self._settings.eval_agent_escalate_model
        )
        grid.addWidget(self._escalate_model_combo, 1, 1)

        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        # Live "Refresh from API" — repopulate the combos with the exact
        # model ids the user's own Gemini key can call, so an invalid id
        # (like the old "gemini-3-pro") can't be picked from the list.
        refresh_row = QHBoxLayout()
        self._refresh_models_btn = QPushButton("↻ Refresh from API")
        self._refresh_models_btn.setToolTip(
            "Fetch the list of Gemini models your saved API key can actually "
            "call, and replace the suggestions with that live list."
        )
        self._refresh_models_btn.clicked.connect(self._on_refresh_models)
        refresh_row.addWidget(self._refresh_models_btn)
        self._models_status = QLabel("")
        self._models_status.setStyleSheet(
            f"color:{theme.ui('subtext')}; font-size:{theme.FONT_SM}px;"
        )
        refresh_row.addWidget(self._models_status, 1)
        layout.addLayout(refresh_row)
        return section

    def _build_model_combo(self, stored: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(_MODEL_SUGGESTIONS)
        # The stored value may be custom (not in the suggestion list);
        # editable combos accept arbitrary current text.
        combo.setCurrentText(stored)
        combo.setStyleSheet(
            f"QComboBox {{"
            f" color:{theme.ui('text')};"
            f" background:{theme.ui('panel_bg')};"
            f" border:1px solid {theme.ui('border')};"
            f" border-radius:{theme.RADIUS_SM}px;"
            f" padding:2px 4px;"
            f"}}"
        )
        return combo

    # ── live model refresh ──────────────────────────────────────────

    def _on_refresh_models(self) -> None:
        """Fetch the live model list off the saved Gemini key (background)."""
        try:
            api_key = self._store.get(GEMINI_API_KEY) if self._store else None
        except Exception:  # noqa: BLE001 — keychain access is best-effort
            api_key = None
        if not api_key:
            if self._models_status is not None:
                self._models_status.setText("Save your Gemini key first, then refresh.")
            return
        if self._refresh_models_btn is not None:
            self._refresh_models_btn.setEnabled(False)
        if self._models_status is not None:
            self._models_status.setText("Fetching available models…")

        def _work(key: str) -> None:
            models = fetch_available_gemini_models(key)
            # Marshal back to the UI thread via the queued signal.
            self._models_fetched.emit(models)

        threading.Thread(target=_work, args=(api_key,), daemon=True).start()

    def _apply_fetched_models(self, models: list[str]) -> None:
        """Repopulate the two combos with *models*, preserving selections."""
        if self._refresh_models_btn is not None:
            self._refresh_models_btn.setEnabled(True)
        if not models:
            if self._models_status is not None:
                self._models_status.setText(
                    "Could not reach the model list (offline or invalid key)."
                )
            return
        for combo in (self._tier_model_combo, self._escalate_model_combo):
            if combo is None:
                continue
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(models)
            combo.setCurrentText(current)  # keep the user's pick even if custom
            combo.blockSignals(False)
        if self._models_status is not None:
            self._models_status.setText(f"Loaded {len(models)} available models.")

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

        # AI-verification model ids (non-secret — plain QSettings).
        # Empty input → leave the stored value unchanged.
        if self._tier_model_combo is not None:
            tier = self._tier_model_combo.currentText().strip()
            if tier and tier != self._settings.eval_agent_tier_model:
                self._settings.eval_agent_tier_model = tier
                changed.add("eval_agent_tier_model")
        if self._escalate_model_combo is not None:
            escalate = self._escalate_model_combo.currentText().strip()
            if escalate and escalate != self._settings.eval_agent_escalate_model:
                self._settings.eval_agent_escalate_model = escalate
                changed.add("eval_agent_escalate_model")

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
