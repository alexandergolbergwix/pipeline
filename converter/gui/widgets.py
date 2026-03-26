"""Custom widgets for the MARC to TTL converter GUI."""

from pathlib import Path
from typing import Optional, List
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QFileDialog, QProgressBar, QFrame,
    QGroupBox, QCheckBox, QSplitter, QPlainTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPalette


class FileSelector(QWidget):
    """Widget for selecting files or directories."""
    
    pathChanged = pyqtSignal(str)
    
    def __init__(self, label: str = "File:", 
                 mode: str = "file",
                 file_filter: str = "All Files (*.*)",
                 parent: Optional[QWidget] = None):
        """Initialize the file selector.
        
        Args:
            label: Label text
            mode: "file" for single file, "files" for multiple, "directory" for folder
            file_filter: File filter for dialog
            parent: Parent widget
        """
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter
        self._setup_ui(label)
    
    def _setup_ui(self, label: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.label = QLabel(label)
        self.label.setMinimumWidth(80)
        layout.addWidget(self.label)
        
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select path...")
        self.path_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.path_edit, 1)
        
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse)
        layout.addWidget(self.browse_btn)
    
    def _browse(self):
        if self.mode == "directory":
            path = QFileDialog.getExistingDirectory(
                self, "Select Directory", 
                str(Path.home())
            )
        elif self.mode == "files":
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Select Files",
                str(Path.home()),
                self.file_filter
            )
            path = ";".join(paths) if paths else ""
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select File",
                str(Path.home()),
                self.file_filter
            )
        
        if path:
            self.path_edit.setText(path)
    
    def _on_text_changed(self, text: str):
        self.pathChanged.emit(text)
    
    def get_path(self) -> str:
        """Get the selected path."""
        return self.path_edit.text()
    
    def get_paths(self) -> List[str]:
        """Get all selected paths (for multi-file mode)."""
        text = self.path_edit.text()
        return [p.strip() for p in text.split(";") if p.strip()]
    
    def set_path(self, path: str):
        """Set the path."""
        self.path_edit.setText(path)


class LogViewer(QGroupBox):
    """Widget for displaying log messages."""
    
    def __init__(self, title: str = "Log", parent: Optional[QWidget] = None):
        super().__init__(title, parent)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        self.text_area = QPlainTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setFont(QFont("Consolas", 10))
        self.text_area.setMaximumBlockCount(10000)
        layout.addWidget(self.text_area)
        
        btn_layout = QHBoxLayout()
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        btn_layout.addWidget(self.clear_btn)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
    
    def log(self, message: str, level: str = "info"):
        """Add a log message.
        
        Args:
            message: Message text
            level: Log level ('info', 'warning', 'error', 'success')
        """
        prefix = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "success": "✅"
        }.get(level, "")
        
        self.text_area.appendPlainText(f"{prefix} {message}")
    
    def info(self, message: str):
        self.log(message, "info")
    
    def warning(self, message: str):
        self.log(message, "warning")
    
    def error(self, message: str):
        self.log(message, "error")
    
    def success(self, message: str):
        self.log(message, "success")
    
    def clear(self):
        """Clear all log messages."""
        self.text_area.clear()


class TtlPreview(QGroupBox):
    """Widget for previewing generated TTL content."""
    
    def __init__(self, title: str = "TTL Preview", parent: Optional[QWidget] = None):
        super().__init__(title, parent)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        self.text_area = QPlainTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setFont(QFont("Consolas", 10))
        self.text_area.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.text_area)
        
        btn_layout = QHBoxLayout()
        
        self.copy_btn = QPushButton("Copy to Clipboard")
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        btn_layout.addWidget(self.copy_btn)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
    
    def _copy_to_clipboard(self):
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_area.toPlainText())
    
    def set_content(self, content: str):
        """Set the TTL content to display."""
        self.text_area.setPlainText(content)
    
    def clear(self):
        """Clear the preview."""
        self.text_area.clear()


class ValidationReport(QGroupBox):
    """Widget for displaying SHACL validation results."""
    
    def __init__(self, title: str = "Validation Results", 
                 parent: Optional[QWidget] = None):
        super().__init__(title, parent)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        self.status_label = QLabel("No validation performed")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        layout.addWidget(self.status_label)
        
        self.details_area = QPlainTextEdit()
        self.details_area.setReadOnly(True)
        self.details_area.setFont(QFont("Consolas", 9))
        layout.addWidget(self.details_area)
    
    def set_result(self, conforms: bool, report: str):
        """Set the validation result.
        
        Args:
            conforms: Whether validation passed
            report: Detailed report text
        """
        if conforms:
            self.status_label.setText("✅ Validation Passed")
            self.status_label.setStyleSheet(
                "font-weight: bold; font-size: 12pt; color: green;"
            )
        else:
            self.status_label.setText("❌ Validation Failed")
            self.status_label.setStyleSheet(
                "font-weight: bold; font-size: 12pt; color: red;"
            )
        
        self.details_area.setPlainText(report)
    
    def clear(self):
        """Clear validation results."""
        self.status_label.setText("No validation performed")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        self.details_area.clear()


class ProgressWidget(QWidget):
    """Widget for showing conversion progress."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
    
    def set_progress(self, value: int, status: str = ""):
        """Set progress value and status.
        
        Args:
            value: Progress percentage (0-100)
            status: Status message
        """
        self.progress_bar.setValue(value)
        if status:
            self.status_label.setText(status)
    
    def set_status(self, status: str):
        """Set status message only."""
        self.status_label.setText(status)
    
    def reset(self):
        """Reset progress to initial state."""
        self.progress_bar.setValue(0)
        self.status_label.setText("Ready")
    
    def set_indeterminate(self, status: str = "Processing..."):
        """Set indeterminate progress mode."""
        self.progress_bar.setMaximum(0)
        self.status_label.setText(status)
    
    def set_determinate(self, max_value: int = 100):
        """Set determinate progress mode."""
        self.progress_bar.setMaximum(max_value)


