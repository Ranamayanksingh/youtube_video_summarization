#!/usr/bin/env bash
# setup.sh — one-shot setup for youtube-summarizer on macOS (Apple Silicon)
set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()    { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  YouTube Summarizer — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. macOS check ──────────────────────────────────────────────────────────
info "Checking platform..."
[[ "$(uname)" == "Darwin" ]] || fail "This project requires macOS."
ARCH="$(uname -m)"
[[ "$ARCH" == "arm64" ]] || warn "Non-Apple Silicon detected ($ARCH). mlx-whisper is optimised for arm64."
success "macOS $(sw_vers -productVersion) on $ARCH"

# ── 2. Homebrew ─────────────────────────────────────────────────────────────
info "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
success "Homebrew $(brew --version | head -1 | awk '{print $2}')"

# ── 3. Python ≥ 3.12 ────────────────────────────────────────────────────────
info "Checking Python..."
if command -v python3 &>/dev/null; then
    PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    PY_MAJOR="${PY_VER%%.*}"
    PY_MINOR="${PY_VER##*.}"
    if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 12 ]]; then
        info "Python $PY_VER found but >=3.12 required. Installing via Homebrew..."
        brew install python@3.12
    fi
fi
PYTHON="$(command -v python3.12 || command -v python3)"
success "Python $($PYTHON --version)"

# ── 4. uv ───────────────────────────────────────────────────────────────────
info "Checking uv..."
if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add to PATH for the rest of this script
    export PATH="$HOME/.local/bin:$PATH"
fi
success "uv $(uv --version)"

# ── 5. FFmpeg ────────────────────────────────────────────────────────────────
info "Checking FFmpeg..."
if ! command -v ffmpeg &>/dev/null; then
    info "Installing FFmpeg via Homebrew..."
    brew install ffmpeg
fi
success "$(ffmpeg -version 2>&1 | head -1)"

# ── 6. Node.js ───────────────────────────────────────────────────────────────
info "Checking Node.js..."
if ! command -v node &>/dev/null; then
    info "Installing Node.js via Homebrew..."
    brew install node
fi
success "Node.js $(node --version)"

# ── 7. Ollama ────────────────────────────────────────────────────────────────
info "Checking Ollama..."
if ! command -v ollama &>/dev/null; then
    info "Installing Ollama via Homebrew..."
    brew install ollama
fi
success "Ollama $(ollama --version 2>/dev/null || echo 'installed')"

# Ensure llama3 model is pulled
info "Checking llama3 model..."
if ! ollama list 2>/dev/null | grep -q "llama3"; then
    info "Pulling llama3 model (~4.7 GB, one-time download)..."
    ollama pull llama3
fi
success "llama3 model available"

# ── 8. Chrome (for YouTube cookies) ─────────────────────────────────────────
info "Checking Google Chrome..."
if [[ ! -d "/Applications/Google Chrome.app" ]]; then
    warn "Google Chrome not found at /Applications/Google Chrome.app"
    warn "Chrome is required for YouTube cookie extraction (bot bypass)."
    warn "Download from: https://www.google.com/chrome/"
    warn "Skipping — install Chrome manually and re-run setup to verify."
else
    success "Google Chrome found"
fi

# ── 9. Virtual environment via uv ────────────────────────────────────────────
info "Setting up Python virtual environment with uv..."
if [[ -d ".venv" ]]; then
    info "Existing .venv found — recreating to ensure clean state..."
    rm -rf .venv
fi
uv venv .venv --python "$PYTHON"
success "Virtual environment created at .venv/"

# ── 10. Install dependencies ─────────────────────────────────────────────────
info "Installing project dependencies from pyproject.toml..."
uv pip install --python .venv/bin/python -e .
success "Dependencies installed"

# ── 11. Create output directories ─────────────────────────────────────────────
info "Creating output directories..."
mkdir -p downloads summaries
success "downloads/ and summaries/ ready"

# ── 12. Verification ──────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PYTHON_VENV=".venv/bin/python"
ERRORS=0

check_import() {
    local module="$1" label="$2"
    if "$PYTHON_VENV" -c "import $module" 2>/dev/null; then
        success "$label importable"
    else
        fail "$label NOT importable — check installation"
        ERRORS=$((ERRORS + 1))
    fi
}

check_import "yt_dlp"     "yt-dlp"
check_import "mlx_whisper" "mlx-whisper"
check_import "ollama"     "ollama"

info "Verifying main.py CLI..."
if "$PYTHON_VENV" main.py --help &>/dev/null; then
    success "main.py --help OK"
else
    warn "main.py --help failed — check for import errors"
    ERRORS=$((ERRORS + 1))
fi

info "Verifying Ollama connectivity..."
if ollama list &>/dev/null; then
    success "Ollama server reachable"
else
    warn "Ollama server not running. Start it with: ollama serve"
    warn "The pipeline will fail at summarization without Ollama running."
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ $ERRORS -eq 0 ]]; then
    echo -e "  ${GREEN}Setup complete.${NC}"
    echo ""
    echo "  Activate the environment:"
    echo "    source .venv/bin/activate"
    echo ""
    echo "  Run the pipeline:"
    echo "    python main.py video '<youtube_url>'"
    echo "    python main.py channel '<channel_url>'"
    echo ""
    echo "  Schedule daily runs at 7 AM IST:"
    echo "    python scheduler.py install --channel '<channel_url>'"
else
    echo -e "  ${RED}Setup completed with $ERRORS error(s). Review output above.${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
