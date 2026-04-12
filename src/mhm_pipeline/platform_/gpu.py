"""Compute device detection for MHM Pipeline."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys

logger = logging.getLogger(__name__)

_DEVICE: str | None = None


def _torch_is_safe_to_import() -> bool:
    """Check if torch can be safely imported without crashing.

    On Windows CI without GPU drivers, ``import torch`` causes a fatal
    access violation (segfault) in DLL loading that cannot be caught by
    Python's exception handler. We check for known unsafe conditions first.
    """
    # If torch is not installed at all, it's safe (ImportError will be caught)
    if importlib.util.find_spec("torch") is None:
        return False

    # On Windows, check if we're in CI (no GPU drivers → DLL crash)
    if sys.platform == "win32" and os.environ.get("CI"):
        logger.info("Windows CI detected — skipping torch import to avoid DLL crash")
        return False

    return True


def get_device(override: str | None = None) -> str:
    """Return the best available compute device string.

    Priority: MPS > CUDA > CPU. Result is cached after first call.
    Falls back to 'cpu' if torch is not installed or unsafe to import.

    Args:
        override: Force a specific device ('mps', 'cuda', 'cpu', 'auto', or None).

    Returns:
        Device string suitable for ``torch.device()``.
    """
    global _DEVICE  # noqa: PLW0603

    if _DEVICE is not None and override is None:
        return _DEVICE

    if override and override != "auto":
        _DEVICE = override
        logger.info("Compute device overridden: %s", _DEVICE)
        return _DEVICE

    if not _torch_is_safe_to_import():
        _DEVICE = "cpu"
        logger.info("Compute device: cpu (torch not available)")
        return _DEVICE

    try:
        import torch  # noqa: PLC0415

        if torch.backends.mps.is_available():
            _DEVICE = "mps"
        elif torch.cuda.is_available():
            _DEVICE = "cuda"
        else:
            _DEVICE = "cpu"
    except Exception:
        logger.warning("torch import failed — defaulting to cpu")
        _DEVICE = "cpu"

    logger.info("Compute device selected: %s", _DEVICE)
    return _DEVICE


def reset_device_cache() -> None:
    """Clear the cached device selection (useful for testing)."""
    global _DEVICE
    _DEVICE = None
