"""Locate the bundled eval-agent and prepare its per-user state dir.

Rule 50 (2026-05-25) — the eval-agent (a sibling project at
``/Users/alexandergo/Documents/Doctorat/eval-agent``) now ships INSIDE
the MHM Pipeline macOS .app and Windows installer. The trust boundary
from Rule 48 is unchanged: no Python imports across the boundary,
subprocess invocation only, communication via the filesystem.

The eval-agent expects to find its ``config/`` + ``rubrics/`` (under
``config/``) relative to its working directory, and writes
``state/runs/<ts>/`` + ``state/cache/`` there too. Because the bundled
tree lives in a read-only location (``Contents/Resources/eval-agent/``
on macOS, ``<install>/eval-agent/`` on Windows), we COPY the read-only
parts into a writable per-user state directory on first run.

Layout:

- **Bundled tree** (read-only): contains ``eval_agent/`` (Python
  package), ``config/`` (YAML + rubrics), ``pyproject.toml``,
  ``init.sh``.
- **User state dir** (writable, ``platformdirs.user_data_dir
  ("MHMPipeline")/eval-agent/``): receives a fresh copy of ``config/``
  on first run, and is the directory used as the subprocess ``cwd``
  so that ``state/runs/`` + ``state/cache/`` land here. The bundled
  Python package itself is NOT copied — we set ``PYTHONPATH`` so the
  interpreter can import it directly from the read-only tree.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from mhm_pipeline.platform_.paths import app_data_dir, bundled_resource_root


def locate_bundled_eval_agent() -> Path:
    """Return the read-only bundled eval-agent root, or the dev path.

    Search order:

    1. ``sys._MEIPASS / eval-agent`` (PyInstaller-frozen Windows bundle).
    2. ``<bundled_resource_root>/eval-agent`` (covers (1) + any dev
       layout where the eval-agent is staged inside the pipeline tree).
    3. macOS .app: walk up to the ``.app`` ancestor and check
       ``Contents/Resources/eval-agent``.
    4. Dev fallback: ``<repo-root>/../eval-agent`` (the sibling project).

    Raises :class:`FileNotFoundError` if no layout resolves.
    """
    candidates: list[Path] = []

    bundled = bundled_resource_root() / "eval-agent"
    candidates.append(bundled)

    # macOS .app: walk up to find the bundle root.
    for parent in Path(__file__).resolve().parents:
        if parent.name.endswith(".app"):
            candidates.append(parent / "Contents" / "Resources" / "eval-agent")
            break

    # Dev fallback — sibling project, two parents above ``src/``.
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root.parent / "eval-agent")

    for cand in candidates:
        try:
            if (cand / "eval_agent" / "cli.py").exists():
                return cand
        except OSError:
            continue

    raise FileNotFoundError(
        "Bundled eval-agent not found. Looked in: "
        + ", ".join(str(c) for c in candidates)
    )


def ensure_user_state_dir(bundled_root: Path | None = None) -> Path:
    """Return the writable per-user eval-agent state dir, creating it on first run.

    On first call:

    * Creates ``platformdirs.user_data_dir("MHMPipeline")/eval-agent/``.
    * Copies ``config/`` (rubrics + default.yaml + schemas) from the
      bundled read-only tree into the user dir.
    * Creates empty ``state/runs/`` and ``state/cache/`` subdirs the
      eval-agent CLI writes into.

    On subsequent calls the function is **idempotent**: returns the
    cached path without re-copying *unless* the bundled tree's
    ``config/`` mtime is strictly newer than the user copy's, in which
    case the config is refreshed (so a re-install rolls out new rubrics
    without nuking the user's accumulated ``state/runs/``).

    Parameters
    ----------
    bundled_root:
        Optional override for the bundled eval-agent root. If
        ``None``, calls :func:`locate_bundled_eval_agent`.
    """
    if bundled_root is None:
        bundled_root = locate_bundled_eval_agent()

    user_dir = app_data_dir() / "eval-agent"
    user_dir.mkdir(parents=True, exist_ok=True)

    bundled_config = bundled_root / "config"
    user_config = user_dir / "config"

    needs_refresh = False
    if not user_config.exists():
        needs_refresh = True
    elif bundled_config.exists():
        try:
            bundled_mtime = bundled_config.stat().st_mtime
            user_mtime = user_config.stat().st_mtime
            if bundled_mtime > user_mtime:
                needs_refresh = True
        except OSError:
            needs_refresh = True

    if needs_refresh and bundled_config.exists():
        if user_config.exists():
            shutil.rmtree(user_config)
        shutil.copytree(bundled_config, user_config)

    (user_dir / "state" / "runs").mkdir(parents=True, exist_ok=True)
    (user_dir / "state" / "cache").mkdir(parents=True, exist_ok=True)

    return user_dir


def resolve_python_executable() -> str:
    """Return the absolute path to the Python interpreter the eval-agent
    subprocess should use.

    Frozen bundles ship their own embedded Python; ``sys.executable``
    points at the bundled binary in that case. In development this is
    just the venv interpreter. Either way ``sys.executable`` is the
    right value — we only wrap it for documentation/testability.
    """
    return sys.executable


__all__ = [
    "ensure_user_state_dir",
    "locate_bundled_eval_agent",
    "resolve_python_executable",
]
