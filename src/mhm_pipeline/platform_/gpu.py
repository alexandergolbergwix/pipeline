"""Compute device detection for MHM Pipeline."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEVICE: str | None = None


def get_device(override: str | None = None) -> str:
    """Return the best available compute device string.

    Priority: MPS > CUDA > CPU. Result is cached after first call.
    Falls back to 'cpu' if torch is not installed.

    Args:
        override: Force a specific device ('mps', 'cuda', 'cpu', 'auto', or None).

    Returns:
        Device string suitable for ``torch.device()``.
    """
    global _DEVICE

    if _DEVICE is not None and override is None:
        return _DEVICE

    if override and override != "auto":
        _DEVICE = override
        logger.info("Compute device overridden: %s", _DEVICE)
        return _DEVICE

    try:
        import torch  # noqa: PLC0415

        if torch.backends.mps.is_available():
            _DEVICE = "mps"
        elif torch.cuda.is_available():
            _DEVICE = "cuda"
        else:
            _DEVICE = "cpu"
    except (ImportError, OSError, RuntimeError, Exception):
        # ImportError: torch not installed
        # OSError/RuntimeError: DLL load failure on Windows CI (no GPU drivers)
        logger.warning("torch not available — defaulting to cpu")
        _DEVICE = "cpu"

    logger.info("Compute device selected: %s", _DEVICE)
    return _DEVICE


def reset_device_cache() -> None:
    """Clear the cached device selection (useful for testing)."""
    global _DEVICE
    _DEVICE = None
