#!/usr/bin/env bash
# Stage a SMALL delta zip for the Windows build host containing only
# the files changed since a baseline commit. The Windows operator
# overlays this on top of the previously-unzipped full source tree
# and runs the installer normally — no need to re-transfer the 13 GB
# full bundle for a code-only change.
#
# Usage:
#   bash scripts/package_windows_delta.sh [baseline_commit]
#
# Default baseline is the commit before the most recent commit chain
# that touched the pipeline (override on the command line for finer
# control). The script never bundles model weights, indexes, or HF
# snapshots — those don't change with code edits.
#
# Output:
#   dist/mhm-pipeline-source-delta.zip   (≈ KB-to-MB scale)
#   dist/mhm-pipeline-source-delta.txt   (manifest + deletion list)

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
BASELINE="${1:-86e6bfb}"
HEAD_REF="$(git rev-parse --short HEAD)"
STAGING="${ROOT}/dist/_windelta"
OUT_ZIP="${ROOT}/dist/mhm-pipeline-source-delta.zip"
OUT_TXT="${ROOT}/dist/mhm-pipeline-source-delta.txt"

echo "=== MHM Pipeline — Windows DELTA bundler ==="
echo "Baseline commit: ${BASELINE}"
echo "Head commit:     ${HEAD_REF}"
echo "Repo root:       ${ROOT}"

rm -rf "$STAGING" "$OUT_ZIP" "$OUT_TXT"
mkdir -p "$STAGING" "${ROOT}/dist"

# ── Collect changed files via git ───────────────────────────────────
echo
echo "[1/3] Computing diff..."
ADDED_OR_MODIFIED="$(git diff --name-status "${BASELINE}" HEAD | grep -E '^[AMR]' | awk '{print $2}')"
DELETED="$(git diff --name-status "${BASELINE}" HEAD | grep -E '^D' | awk '{print $2}')"

NUM_AM=$(echo "$ADDED_OR_MODIFIED" | grep -c . || true)
NUM_DEL=$(echo "$DELETED" | grep -c . || true)
echo "  Added / modified: $NUM_AM"
echo "  Deleted:          $NUM_DEL"

# ── Copy added/modified files preserving paths ─────────────────────
echo
echo "[2/3] Staging changed files..."
if [ -n "$ADDED_OR_MODIFIED" ]; then
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        # Skip paper/docs/tests dirs the Windows installer doesn't use
        case "$f" in
            paper/*|docs/presentations/*|docs/cv/*|tests/*)
                continue
                ;;
        esac
        if [ -f "$f" ]; then
            mkdir -p "$STAGING/$(dirname "$f")"
            cp "$f" "$STAGING/$f"
            echo "  +$f"
        fi
    done <<< "$ADDED_OR_MODIFIED"
fi

# ── Stage the bundled eval-agent (Rule 50, 2026-05-25) ─────────────
# The eval-agent ships INSIDE the bundle (Rule 50); the Windows
# installer's MHMPipeline.spec picks it up via _opt_dir('eval-agent/...').
# The sibling project at ../eval-agent isn't in this repo, so the
# git-diff path above can't see it — we always stage a fresh snapshot
# into the delta zip's _windelta-eval/ folder. Operator copies
# _windelta-eval\eval-agent INTO mhm-pipeline-source\ during apply.
EVAL_AGENT_SRC="${EVAL_AGENT_ROOT:-${ROOT}/../eval-agent}"
EVAL_AGENT_STAGED=""
if [ -d "$EVAL_AGENT_SRC/eval_agent" ]; then
    EVAL_STAGE="${ROOT}/dist/_windelta-eval"
    rm -rf "$EVAL_STAGE"
    mkdir -p "$EVAL_STAGE/eval-agent"
    rsync -a \
        --exclude '.git' --exclude '.venv' --exclude 'state' \
        --exclude 'tests' --exclude '__pycache__' --exclude '*.pyc' \
        --exclude '*.swp' \
        "$EVAL_AGENT_SRC/eval_agent" "$EVAL_STAGE/eval-agent/"
    if [ -d "$EVAL_AGENT_SRC/config" ]; then
        rsync -a --exclude '__pycache__' --exclude '*.pyc' \
            "$EVAL_AGENT_SRC/config" "$EVAL_STAGE/eval-agent/"
    fi
    if [ -f "$EVAL_AGENT_SRC/pyproject.toml" ]; then
        cp "$EVAL_AGENT_SRC/pyproject.toml" "$EVAL_STAGE/eval-agent/"
    fi
    EVAL_AGENT_STAGED="yes"
    echo "  staged eval-agent → _windelta-eval/eval-agent ($(du -sh "$EVAL_STAGE" | cut -f1))"
fi

# ── Write a manifest + deletion list ───────────────────────────────
echo
echo "[3/3] Writing manifest..."
{
    echo "MHM Pipeline — Windows source delta"
    echo "===================================="
    echo "Baseline commit: ${BASELINE}"
    echo "Head commit:     ${HEAD_REF}"
    echo "Generated:       $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo ""
    echo "How to apply on the Windows build host"
    echo "--------------------------------------"
    echo "1. Make sure the previous full unzip (mhm-pipeline-source/) is"
    echo "   still in place from the previous transfer."
    echo "2. Unzip this delta. It contains two top-level folders:"
    echo "     _windelta/       → pipeline source overlay"
    if [ -n "$EVAL_AGENT_STAGED" ]; then
        echo "     _windelta-eval/  → eval-agent source (Rule 50)"
    fi
    echo "3. Copy _windelta\\* INTO mhm-pipeline-source\\ (overwrite files)."
    if [ -n "$EVAL_AGENT_STAGED" ]; then
        echo "4. Copy _windelta-eval\\eval-agent INTO mhm-pipeline-source\\"
        echo "   so MHMPipeline.spec picks it up as eval-agent\\eval_agent"
        echo "   and eval-agent\\config (Rule 50 bundles eval-agent inside"
        echo "   the Windows installer)."
    fi
    echo "5. Delete the files listed below (they were retired):"
    echo ""
    if [ -n "$DELETED" ]; then
        echo "$DELETED" | sed 's/^/    DEL /'
    else
        echo "    (no deletions)"
    fi
    echo ""
    echo "6. Re-run installer\\windows\\Build Installer.bat as usual."
    echo ""
    echo "Files included in this delta:"
    echo "-----------------------------"
    if [ -n "$ADDED_OR_MODIFIED" ]; then
        echo "$ADDED_OR_MODIFIED" | grep -vE '^(paper/|docs/presentations/|docs/cv/|tests/)' \
            | sed 's/^/    /'
    fi
    echo ""
    echo "Commit summary:"
    echo "---------------"
    git log --oneline "${BASELINE}..HEAD"
} > "$OUT_TXT"

cd "${ROOT}/dist"
if [ -n "$EVAL_AGENT_STAGED" ]; then
    zip -r -q "$OUT_ZIP" "_windelta/" "_windelta-eval/"
    rm -rf "$STAGING" "$EVAL_STAGE"
else
    zip -r -q "$OUT_ZIP" "_windelta/"
    rm -rf "$STAGING"
fi

SIZE="$(du -h "$OUT_ZIP" | cut -f1)"
echo
echo "=== DONE ==="
echo "Delta zip:  $OUT_ZIP ($SIZE)"
echo "Manifest:   $OUT_TXT"
echo
echo "Next steps:"
echo "  1. Send $OUT_ZIP + $OUT_TXT to the Windows host"
echo "  2. Operator unzips the delta INTO the existing source folder"
echo "     (it will overwrite changed files in place)"
echo "  3. Operator deletes the files listed in the manifest"
echo "  4. Re-run installer\\windows\\Build Installer.bat as usual"
