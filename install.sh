#!/usr/bin/env bash
# leantext installer
# Usage: ./install.sh            (asks about Office support and the Claude Code hook)
#        ./install.sh --yes      (sets up the Claude Code hook without asking)
#        ./install.sh --no-hook  (skips the Claude Code hook)
#        ./install.sh --office   (adds Word/Excel/PowerPoint support, ~40 MB more)
#        ./install.sh --ocr      (adds RapidOCR as a backup OCR engine on macOS)
set -euo pipefail

HOOK_MODE="ask"
OFFICE_MODE="ask"
EXTRAS="tokens"
for arg in "$@"; do
  case "$arg" in
    --yes|-y)  HOOK_MODE="yes" ;;
    --no-hook) HOOK_MODE="no" ;;
    --office)  OFFICE_MODE="yes" ;;
    --ocr)     EXTRAS="$EXTRAS,ocr" ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

PYTHON_VERSION="3.12"   # uv downloads this automatically if missing
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
step() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗ %s\033[0m\n" "$1"; exit 1; }

# 1. System tools: none required. Use Tesseract and pdftotext if they're already installed.
step "Checking optional tools"
case "$(uname -s)" in
  Darwin|Linux) ;;
  *) fail "Unsupported OS: $(uname -s). macOS and Linux are supported." ;;
esac
if command -v tesseract >/dev/null; then
  ok "tesseract found: used for OCR (best on terminal and code screenshots)"
elif [ "$(uname -s)" = "Darwin" ]; then
  ok "OCR: Apple Vision (built into macOS). Optional, for code screenshots: brew install tesseract"
else
  ok "OCR: RapidOCR (installed with leantext). Optional, for code screenshots: sudo apt install tesseract-ocr"
fi
if command -v pdftotext >/dev/null; then
  ok "pdftotext found: used for two-column PDFs"
else
  ok "PDFs: pypdf (installed with leantext)"
fi

# 2. uv (fast installer that brings its own compatible Python)
step "Setting up uv"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null || fail "uv install failed. See https://docs.astral.sh/uv/ and re-run."
uv tool update-shell >/dev/null 2>&1 || true
ok "uv ready (~/.local/bin is on PATH for new terminals)"

# 3. leantext itself, on a Python version its dependencies support
if [ "$OFFICE_MODE" = "ask" ] && [ -t 0 ]; then
  printf "\nAdd support for Word, Excel and PowerPoint files? (about 40 MB more to download) [y/N] "
  read -r reply
  case "$reply" in [yY]*) OFFICE_MODE="yes" ;; *) OFFICE_MODE="no" ;; esac
fi
[ "$OFFICE_MODE" = "yes" ] && EXTRAS="$EXTRAS,office"

step "Installing leantext"
uv tool install --force --python "$PYTHON_VERSION" "${REPO_DIR}[${EXTRAS}]"
command -v leantext >/dev/null || fail "leantext installed but isn't on PATH. Open a new terminal and run: leantext --help"
ok "leantext installed"
if [ "$OFFICE_MODE" = "yes" ]; then
  ok "Office files: Word, Excel, PowerPoint supported"
elif command -v pandoc >/dev/null; then
  ok "Office files: via pandoc (for MarkItDown instead, re-run with --office)"
else
  ok "Office files: not installed (add later with: ./install.sh --office)"
fi

# 3b. Warm up OCR. On macOS the first Apple Vision call waits ~30s while the system loads its
#     text-recognition models; doing it here keeps that wait out of the user's first real read.
if [ "$(uname -s)" = "Darwin" ] && [ -f "$REPO_DIR/tests/fixtures/screenshot.png" ]; then
  echo "  Warming up Apple Vision OCR (first run only, up to 30 seconds)..."
  leantext "$REPO_DIR/tests/fixtures/screenshot.png" --no-cache --no-vision >/dev/null 2>&1 || true
  ok "OCR ready"
fi

# 4. Optional Claude Code hook
setup_hook() {
  local settings="$HOME/.claude/settings.json"
  local tmp; tmp="$(mktemp)"
  mkdir -p "$HOME/.claude"
  leantext hook-config > "$tmp"
  [ -f "$settings" ] && cp "$settings" "$settings.bak.$(date +%Y%m%d%H%M%S)"
  python3 - "$settings" "$tmp" <<'PY'
import json, os, sys
settings_path, hook_path = sys.argv[1], sys.argv[2]
settings = {}
if os.path.exists(settings_path) and os.path.getsize(settings_path) > 0:
    with open(settings_path) as f:
        settings = json.load(f)
with open(hook_path) as f:
    new = json.load(f)
hooks = settings.setdefault("hooks", {})
for event, entries in new.get("hooks", {}).items():
    existing = hooks.setdefault(event, [])
    for entry in entries:
        if entry not in existing:   # keep existing hooks, avoid duplicates
            existing.append(entry)
with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
PY
  rm -f "$tmp"
  ok "Hook added to $settings (existing hooks kept, backup saved)"
  echo "  Restart Claude Code, then run /hooks to confirm."
}

if [ "$HOOK_MODE" = "ask" ] && [ -t 0 ]; then
  printf "\nSet up the Claude Code hook now? [Y/n] "
  read -r reply
  case "$reply" in [nN]*) HOOK_MODE="no" ;; *) HOOK_MODE="yes" ;; esac
elif [ "$HOOK_MODE" = "ask" ]; then
  HOOK_MODE="no"
fi

if [ "$HOOK_MODE" = "yes" ]; then
  step "Setting up Claude Code hook"
  setup_hook
fi

# Done
step "Done!"
echo "  Try it:"
echo "    leantext https://pypi.org/project/numpy/ --report"
echo
echo "  Turn the hook off anytime with LEANTEXT_HOOK=off"
