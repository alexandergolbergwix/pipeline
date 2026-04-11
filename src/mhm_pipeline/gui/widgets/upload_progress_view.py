"""Widget showing Wikidata upload progress with per-entity status tracking."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# Status icons for entity upload states
_STATUS_ICONS: dict[str, str] = {
    "pending": "⏳",
    "uploading": "⟳",
    "success": "✓",
    "updated": "↑",
    "exists": "=",
    "failed": "✗",
    "skipped": "⊘",
}

# Status colors for visual feedback
_STATUS_COLORS: dict[str, str] = {
    "pending": "#888888",
    "uploading": "#3280F0",
    "success": "#3CB44B",
    "updated": "#F0A030",
    "exists": "#888888",
    "failed": "#DC3232",
    "skipped": "#888888",
}


@dataclass
class WikidataEntity:
    """Represents an entity to be uploaded to Wikidata.

    Attributes:
        entity_type: Type of entity (e.g., "Person", "Place", "Work")
        label: Display label for the entity
        local_id: Local identifier from the source data
        qid: Wikidata QID if already exists (optional)

    """

    entity_type: str
    label: str
    local_id: str
    qid: str | None = None


class EntityProgressWidget(QWidget):
    """Widget showing progress for a single entity upload.

    Displays status icon, entity type/label, progress bar, and action buttons.
    Updates dynamically based on upload status changes.

    """

    def __init__(self, entity: WikidataEntity, parent: QWidget | None = None) -> None:
        """Initialize the entity progress widget.

        Args:
            entity: The Wikidata entity to track
            parent: Parent widget (optional)

        """
        super().__init__(parent)
        self._entity = entity
        self._current_status = "pending"

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the widget UI components."""
        # Main layout - vertical
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Header row: icon + type/label + progress bar
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        # Status icon
        self._status_icon = QLabel(_STATUS_ICONS["pending"])
        self._status_icon.setStyleSheet(f"color: {_STATUS_COLORS['pending']};")
        self._status_icon.setFixedWidth(20)
        header_layout.addWidget(self._status_icon)

        # Entity type and label
        self._entity_label = QLabel(f"{self._entity.entity_type}: {self._entity.label}")
        self._entity_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header_layout.addWidget(self._entity_label)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(200)
        self._progress_bar.setMinimumWidth(120)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        header_layout.addWidget(self._progress_bar)

        layout.addLayout(header_layout)

        # Details row: QID link + action buttons
        details_layout = QHBoxLayout()
        details_layout.setSpacing(8)
        details_layout.setContentsMargins(28, 0, 0, 0)  # Indent to align with label

        # QID label (shows link when complete)
        self._qid_label = QLabel()
        self._qid_label.setOpenExternalLinks(True)
        details_layout.addWidget(self._qid_label)

        # Status message label
        self._status_message = QLabel()
        self._status_message.setStyleSheet("color: #666666; font-style: italic;")
        details_layout.addWidget(self._status_message)

        details_layout.addStretch()

        # View button (shows when uploaded)
        self._view_button = QPushButton("View on Wikidata")
        self._view_button.setVisible(False)
        self._view_button.clicked.connect(self._on_view_clicked)
        details_layout.addWidget(self._view_button)

        # Edit button (shows when uploaded)
        self._edit_button = QPushButton("Edit")
        self._edit_button.setVisible(False)
        details_layout.addWidget(self._edit_button)

        # Retry button (shows on failure)
        self._retry_button = QPushButton("Retry")
        self._retry_button.setVisible(False)
        details_layout.addWidget(self._retry_button)

        # Skip button (shows on failure)
        self._skip_button = QPushButton("Skip")
        self._skip_button.setVisible(False)
        details_layout.addWidget(self._skip_button)

        layout.addLayout(details_layout)

        # Store callbacks
        self._view_callback: Callable[[], None] | None = None
        self._retry_callback: Callable[[], None] | None = None

    @property
    def entity(self) -> WikidataEntity:
        """Return the entity being tracked."""
        return self._entity

    @property
    def current_status(self) -> str:
        """Return the current upload status."""
        return self._current_status

    def set_progress(self, value: int) -> None:
        """Update the progress bar value.

        Args:
            value: Progress percentage (0-100)

        """
        self._progress_bar.setValue(max(0, min(100, value)))

    def set_status(
        self,
        status: str,
        qid: str | None = None,
        message: str | None = None,
    ) -> None:
        """Update the upload status with visual feedback.

        Args:
            status: One of pending, uploading, success, exists, failed, skipped
            qid: Wikidata QID if upload successful
            message: Optional status message to display

        """
        self._current_status = status

        # Update icon
        icon = _STATUS_ICONS.get(status, "❓")
        self._status_icon.setText(icon)

        # Update color
        color = _STATUS_COLORS.get(status, "#888888")
        self._status_icon.setStyleSheet(f"color: {color};")

        # Handle status-specific UI updates
        if status == "uploading":
            self._progress_bar.setValue(0)
            self._status_message.setText(message or "Uploading...")

        elif status in ("success", "exists"):
            self._progress_bar.setValue(100)
            display_qid = qid or self._entity.qid
            if display_qid:
                self._entity_label.setText(
                    f"{self._entity.entity_type}: {self._entity.label} ({self._entity.local_id})"
                )
                self._qid_label.setText(
                    f"<a href='https://www.wikidata.org/wiki/{display_qid}'"
                    f" style='color: {color};'>{display_qid}</a>"
                )
                self._view_button.setVisible(True)
                self._edit_button.setVisible(True)
            if status == "exists":
                self._status_message.setText("Already exists")
            else:
                self._status_message.setText("")
            self._retry_button.setVisible(False)
            self._skip_button.setVisible(False)

        elif status == "failed":
            self._progress_bar.setValue(0)
            self._status_message.setText(message or "Upload failed")
            self._status_message.setStyleSheet("color: #DC3232; font-style: italic;")
            self._retry_button.setVisible(True)
            self._skip_button.setVisible(True)

        elif status == "skipped":
            self._progress_bar.setValue(0)
            self._status_message.setText(message or "Skipped")

        elif status == "pending":
            self._progress_bar.setValue(0)
            self._status_message.setText("Waiting...")

    def set_view_callback(self, callback: Callable[[], None]) -> None:
        """Set the callback for the View button.

        Args:
            callback: Function to call when View is clicked

        """
        self._view_callback = callback

    def set_retry_callback(self, callback: Callable[[], None]) -> None:
        """Set the callback for the Retry button.

        Args:
            callback: Function to call when Retry is clicked

        """
        self._retry_callback = callback
        self._retry_button.clicked.connect(callback)

    def _on_view_clicked(self) -> None:
        """Handle view button click."""
        if self._view_callback:
            self._view_callback()


class UploadProgressView(QWidget):
    """Widget showing Wikidata upload progress with per-entity tracking.

    Displays an overall progress bar and a scrollable list of entity widgets,
    each showing individual upload progress, status, and action buttons.

    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the upload progress view.

        Args:
            parent: Parent widget (optional)

        """
        super().__init__(parent)
        self._entity_widgets: list[EntityProgressWidget] = []

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the widget UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Title
        title = QLabel("Wikidata Upload Progress")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

        # Overall progress section
        overall_layout = QHBoxLayout()
        overall_layout.setSpacing(8)

        overall_label = QLabel("Overall:")
        overall_layout.addWidget(overall_label)

        self._overall_progress = QProgressBar()
        self._overall_progress.setTextVisible(True)
        self._overall_progress.setFormat("%p% (%v/%m items)")
        overall_layout.addWidget(self._overall_progress, stretch=1)

        self._status_summary = QLabel("0/0 items")
        overall_layout.addWidget(self._status_summary)

        layout.addLayout(overall_layout)

        # Scroll area for entity list
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QScrollArea.Shape.StyledPanel)

        # Container for entity widgets
        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setSpacing(4)
        self._container_layout.setContentsMargins(4, 4, 4, 4)
        self._container_layout.addStretch()

        self._scroll_area.setWidget(self._container)
        layout.addWidget(self._scroll_area, stretch=1)

        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._export_button = QPushButton("Export QuickStatements")
        self._export_button.setEnabled(False)
        button_layout.addWidget(self._export_button)

        self._pause_button = QPushButton("Pause")
        button_layout.addWidget(self._pause_button)

        self._cancel_button = QPushButton("Cancel")
        button_layout.addWidget(self._cancel_button)

        layout.addLayout(button_layout)

    def add_entity(self, entity: WikidataEntity) -> EntityProgressWidget:
        """Add an entity to the upload queue.

        Args:
            entity: The Wikidata entity to track

        Returns:
            EntityProgressWidget for updating status

        """
        widget = EntityProgressWidget(entity)
        self._entity_widgets.append(widget)

        # Insert before the stretch
        self._container_layout.insertWidget(self._container_layout.count() - 1, widget)

        self._update_summary()
        return widget

    def update_overall_progress(self, completed: int, total: int) -> None:
        """Update the overall progress bar.

        Args:
            completed: Number of completed uploads
            total: Total number of entities to upload

        """
        self._overall_progress.setMaximum(max(1, total))
        self._overall_progress.setValue(completed)
        self._status_summary.setText(f"{completed}/{total} items")

    def _update_summary(self) -> None:
        """Update the status summary based on entity widgets."""
        total = len(self._entity_widgets)
        if total == 0:
            self._status_summary.setText("0/0 items")
            return

        completed = sum(
            1 for w in self._entity_widgets if w.current_status in ("success", "updated", "exists", "failed", "skipped")
        )
        self.update_overall_progress(completed, total)

    def get_entity_widget(self, local_id: str) -> EntityProgressWidget | None:
        """Get an entity widget by its local ID.

        Args:
            local_id: The local identifier to search for

        Returns:
            EntityProgressWidget if found, None otherwise

        """
        for widget in self._entity_widgets:
            if widget.entity.local_id == local_id:
                return widget
        return None

    def clear_entities(self) -> None:
        """Remove all entity widgets and reset the view."""
        for widget in self._entity_widgets:
            widget.deleteLater()
        self._entity_widgets.clear()
        self._update_summary()

    def set_export_callback(self, callback: Callable[[], None]) -> None:
        """Set the callback for the Export QuickStatements button.

        Args:
            callback: Function to call when Export is clicked

        """
        self._export_button.clicked.connect(callback)
        self._export_button.setEnabled(True)

    def set_pause_callback(self, callback: Callable[[], None]) -> None:
        """Set the callback for the Pause button.

        Args:
            callback: Function to call when Pause is clicked

        """
        self._pause_button.clicked.connect(callback)

    def set_cancel_callback(self, callback: Callable[[], None]) -> None:
        """Set the callback for the Cancel button.

        Args:
            callback: Function to call when Cancel is clicked

        """
        self._cancel_button.clicked.connect(callback)

    def enable_export_button(self, enabled: bool) -> None:
        """Enable or disable the Export QuickStatements button.

        Args:
            enabled: Whether to enable the button

        """
        self._export_button.setEnabled(enabled)
