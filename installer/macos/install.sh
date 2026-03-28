#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/Applications/MHMPipeline
LAUNCHER_DIR=/Applications/MHMPipeline.app/Contents/MacOS

# Download and install uv if not present
if [ ! -f "$APP_DIR/bin/uv" ]; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$APP_DIR/bin" sh
fi

# Install Python 3.12 via uv
echo "Installing Python 3.12..."
"$APP_DIR/bin/uv" python install 3.12

# Install project dependencies
echo "Installing project dependencies..."
cd "$APP_DIR"
"$APP_DIR/bin/uv" sync --frozen --no-dev

# Create launcher shell script
echo "Creating application launcher..."
mkdir -p "$LAUNCHER_DIR"
cat > "$LAUNCHER_DIR/mhm_pipeline" << 'LAUNCHER'
#!/usr/bin/env bash
exec /Applications/MHMPipeline/.venv/bin/python -m mhm_pipeline.app "$@"
LAUNCHER

chmod +x "$LAUNCHER_DIR/mhm_pipeline"

echo "MHM Pipeline installed successfully."
