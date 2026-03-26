#!/usr/bin/env python3
"""
Hebrew Manuscripts Converter - Application Launcher
Converts MARC/CSV/TSV data to RDF TTL format based on the Hebrew Manuscripts Ontology.
"""

import sys
import os

# Ensure the converter package is importable
if getattr(sys, 'frozen', False):
    # Running as compiled app
    application_path = os.path.dirname(sys.executable)
    os.chdir(application_path)
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from converter.gui.main_window import MainWindow


def main():
    """Launch the Hebrew Manuscripts Converter GUI."""
    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    
    app = QApplication(sys.argv)
    app.setApplicationName("Hebrew Manuscripts Converter")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("Hebrew Manuscripts Ontology Project")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

