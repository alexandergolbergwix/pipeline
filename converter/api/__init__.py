"""
API module for native app integration.

Provides a clean interface for SwiftUI (macOS) and WinUI 3 (Windows) apps
to call the Python conversion logic.
"""

from .converter_api import (
    ConversionResult,
    convert_file,
    get_supported_formats,
    get_version,
    validate_file,
)

__all__ = [
    "convert_file",
    "get_supported_formats",
    "validate_file",
    "get_version",
    "ConversionResult",
]
