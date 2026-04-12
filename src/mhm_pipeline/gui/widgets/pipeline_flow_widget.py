"""Pipeline flow widget showing six stage boxes with animated active state."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from mhm_pipeline.gui.widgets.base_visualization_widget import is_dark_mode


class PipelineFlowWidget(QWidget):
    """Widget showing pipeline flow overview with 6 interactive stage boxes.

    Displays six stages horizontally connected by arrows:
    - Parse → NER → Authority → RDF → Validate → Upload

    Each stage has:
    - An icon button with the stage name
    - A stats label showing current metrics
    - Visual state: completed (green), active (animated), future (gray)

    Signals:
        stage_clicked(int): Emitted when a stage button is clicked,
            passing the stage index (0-5).

    Example:
        >>> widget = PipelineFlowWidget()
        >>> widget.set_active_stage(2)  # Highlight stage 3
        >>> widget.update_stage_stats(0, "47 fields")
    """

    STAGE_CONFIG: list[dict[str, str]] = [
        {"name": "Parse", "icon": "📄", "color": "#f59e0b"},
        {"name": "NER", "icon": "🔍", "color": "#eab308"},
        {"name": "Authority", "icon": "🔗", "color": "#8b5cf6"},
        {"name": "RDF", "icon": "🕸️", "color": "#3b82f6"},
        {"name": "Validate", "icon": "✓", "color": "#22c55e"},
        {"name": "Upload", "icon": "☁️", "color": "#ef4444"},
    ]

    # Animation colors for active stage (alternating shades)
    _ANIM_COLORS: list[str] = ["#fef3c7", "#fde68a"]  # Light amber shades
    _ANIM_COLORS_DARK: list[str] = ["#92400e", "#b45309"]  # Dark amber shades

    # Completed stage background color (light green)
    _COMPLETED_BG: str = "#dcfce7"
    _COMPLETED_BG_DARK: str = "#14532d"  # Dark green

    # Default/future stage background color
    _DEFAULT_BG: str = "#f3f4f6"
    _DEFAULT_BG_DARK: str = "#374151"  # Dark gray
    _DEFAULT_HOVER_DARK: str = "#4b5563"  # Darker gray for hover

    stage_clicked: pyqtSignal = pyqtSignal(int)
    """Signal emitted when a stage button is clicked.

    Args:
        int: The index of the clicked stage (0-5).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the pipeline flow widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._stage_buttons: list[QPushButton] = []
        self._stage_labels: list[QLabel] = []
        self._current_stage: int = -1
        self._anim_frame: int = 0

        self._build_ui()

        # Animation timer for active stage pulsing
        self._anim_timer: QTimer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate_active_stage)

    def _build_ui(self) -> None:
        """Build the UI with 6 stage boxes connected by arrows."""
        layout: QHBoxLayout = QHBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 8, 8, 8)

        for i, config in enumerate(self.STAGE_CONFIG):
            # Stage container
            stage_widget: QWidget = QWidget()
            stage_layout: QVBoxLayout = QVBoxLayout(stage_widget)
            stage_layout.setSpacing(4)
            stage_layout.setContentsMargins(0, 0, 0, 0)

            # Icon button with stage name
            btn: QPushButton = QPushButton(f"{config['icon']}\n{config['name']}")
            btn.setFixedSize(80, 60)
            self._update_button_style(btn, config["color"], "default")
            btn.clicked.connect(lambda checked, idx=i: self.stage_clicked.emit(idx))

            # Stats label
            lbl: QLabel = QLabel("Waiting...")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size: 10px;")

            stage_layout.addWidget(btn)
            stage_layout.addWidget(lbl)

            layout.addWidget(stage_widget)
            self._stage_buttons.append(btn)
            self._stage_labels.append(lbl)

            # Arrow between stages (except after last)
            if i < len(self.STAGE_CONFIG) - 1:
                arrow: QLabel = QLabel("→")
                arrow.setStyleSheet("font-size: 20px; color: palette(mid);")
                layout.addWidget(arrow)

        layout.addStretch()

    def set_active_stage(self, stage_index: int) -> None:
        """Set which stage is currently active with animation.

        Completed stages (before active) are shown with green background.
        The active stage gets an animated pulsing color.
        Future stages remain in default state.

        Args:
            stage_index: Index of the active stage (0-5), or -1 to clear.

        Example:
            >>> widget.set_active_stage(2)  # Stage 3 is active
        """
        self._current_stage = stage_index
        self._anim_frame = 0

        # Stop any existing animation
        self._anim_timer.stop()

        for i, btn in enumerate(self._stage_buttons):
            config: dict[str, str] = self.STAGE_CONFIG[i]
            color: str = config["color"]

            if i < stage_index:
                # Completed stage
                self._update_button_style(btn, color, "completed")
            elif i == stage_index:
                # Active stage - start animation
                self._update_button_style(btn, color, "active")
                self._anim_timer.start(500)  # Toggle every 500ms
            else:
                # Future stage - default background
                self._update_button_style(btn, color, "default")

    def update_stage_stats(self, stage_index: int, stats: str) -> None:
        """Update the statistics display for a specific stage.

        Args:
            stage_index: Index of the stage to update (0-5).
            stats: Statistics string (e.g., "47 fields", "12 entities").

        Raises:
            IndexError: If stage_index is out of range (0-5).

        Example:
            >>> widget.update_stage_stats(0, "47 fields")
            >>> widget.update_stage_stats(1, "12 entities")
        """
        if not 0 <= stage_index < len(self._stage_labels):
            raise IndexError(f"Stage index {stage_index} out of range (0-5)")
        self._stage_labels[stage_index].setText(stats)

    def _animate_active_stage(self) -> None:
        """Animate the active stage button with pulsing colors.

        Alternates between two shades every 500ms, adapting for dark mode.
        """
        if self._current_stage < 0 or self._current_stage >= len(self._stage_buttons):
            return

        btn: QPushButton = self._stage_buttons[self._current_stage]
        config: dict[str, str] = self.STAGE_CONFIG[self._current_stage]
        color: str = config["color"]

        dark = is_dark_mode(self)
        anim_colors = self._ANIM_COLORS_DARK if dark else self._ANIM_COLORS

        current_color: str = anim_colors[self._anim_frame % 2]
        next_color: str = anim_colors[(self._anim_frame + 1) % 2]

        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {next_color};
                border: 2px solid {color};
                border-radius: 8px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {current_color};
            }}
        """)
        self._anim_frame += 1

    def reset(self) -> None:
        """Reset all stages to initial state.

        Clears the active stage, stops animation, and resets all
        stage buttons to their default appearance.
        """
        self._current_stage = -1
        self._anim_frame = 0
        self._anim_timer.stop()

        for i, btn in enumerate(self._stage_buttons):
            config: dict[str, str] = self.STAGE_CONFIG[i]
            color: str = config["color"]
            self._update_button_style(btn, color, "default")

        for lbl in self._stage_labels:
            lbl.setText("Waiting...")

    def _get_stage_colors(self, state: str) -> tuple[str, str]:
        """Get background and hover colors for a stage based on state and theme.

        Args:
            state: One of 'default', 'completed', 'active', 'future'.

        Returns:
            Tuple of (background_color, hover_color).
        """
        dark = is_dark_mode(self)

        if state == "completed":
            bg = self._COMPLETED_BG_DARK if dark else self._COMPLETED_BG
            hover = "#166534" if dark else "#bbf7d0"
        elif state == "active":
            # Use amber colors
            bg = self._ANIM_COLORS_DARK[0] if dark else self._ANIM_COLORS[0]
            hover = self._ANIM_COLORS_DARK[1] if dark else self._ANIM_COLORS[1]
        else:  # default/future
            bg = self._DEFAULT_BG_DARK if dark else self._DEFAULT_BG
            hover = self._DEFAULT_HOVER_DARK if dark else "#e5e7eb"

        return bg, hover

    def _update_button_style(self, btn: QPushButton, border_color: str, state: str) -> None:
        """Update a button's stylesheet with appropriate colors.

        Args:
            btn: The button to update.
            border_color: The border color (stage color).
            state: One of 'default', 'completed', 'active'.
        """
        bg, hover = self._get_stage_colors(state)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                border: 2px solid {border_color};
                border-radius: 8px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
        """)

    def get_current_stage(self) -> int:
        """Return the index of the currently active stage.

        Returns:
            Index of active stage (0-5), or -1 if no stage is active.
        """
        return self._current_stage
