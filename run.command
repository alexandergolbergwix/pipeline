#!/usr/bin/env bash
# Double-click this file in Finder to launch MHM Pipeline.
# The .command extension makes macOS run it in Terminal automatically.
cd "$(dirname "$0")"
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app
