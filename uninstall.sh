#!/usr/bin/env bash
# leantext uninstaller
# Usage: ./uninstall.sh          (removes leantext, its hook and cache; asks about system tools)
#        ./uninstall.sh --all    (also removes tesseract, poppler and pipx without asking)
set -uo pipefail

REMOVE_TOOLS="ask"
for arg in "$@"; do
  case "$arg" in
    --all) REMOVE_TOOLS="yes" ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

export PATH="$HOME/.local/bin:$PATH"
step() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
skip() { printf "  - %s\n" "$1"; }

# 1. Claude Code hook (removes only leantext's entries, keeps everything else)
step "Removing the Claude Code hook"
removed_any=0
for settings in "$HOME/.claude/settings.json" ".claude/settings.json"; do
  [ -f "$settings" ] || continue
  cp "$settings" "$settings.bak.$(date +%Y%m%d%H%M%S)"
  if python3 - "$settings" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    settings = json.load(f)
hooks = settings.get("hooks", {})
changed = False
for event in list(hooks):
    kept = []
    for entry in hooks[event]:
        if "leantext" in json.dumps(entry):
            changed = True
        else:
            kept.append(entry)
    if kept:
        hooks[event] = kept
    else:
        del hooks[event]
if "hooks" in settings and not settings["hooks"]:
    del settings["hooks"]
if changed:
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
sys.exit(0 if changed else 1)
PY
  then
    ok "Removed leantext hook from $settings (backup saved)"
    removed_any=1
  fi
done
[ "$removed_any" = 1 ] && echo "  Restart Claude Code to apply." || skip "No leantext hook found"

# 2. leantext itself
step "Removing leantext"
if command -v pipx >/dev/null && pipx list 2>/dev/null | grep -q "leantext"; then
  pipx uninstall leantext >/dev/null && ok "leantext uninstalled"
elif command -v uv >/dev/null && uv tool list 2>/dev/null | grep -q "^leantext"; then
  uv tool uninstall leantext >/dev/null && ok "leantext uninstalled (uv)"
else
  skip "leantext not installed"
fi

# 3. Cache and logs
step "Removing cache and logs"
if [ -d "$HOME/.cache/leantext" ]; then
  rm -rf "$HOME/.cache/leantext" && ok "Removed ~/.cache/leantext"
else
  skip "No cache found"
fi

# 4. System tools (optional; you may use these for other things)
step "System tools (tesseract, poppler, pipx)"
if [ "$REMOVE_TOOLS" = "ask" ]; then
  if [ -t 0 ]; then
    printf "  Remove them too? Only say yes if nothing else on this machine uses them. [y/N] "
    read -r reply
    case "$reply" in [yY]*) REMOVE_TOOLS="yes" ;; *) REMOVE_TOOLS="no" ;; esac
  else
    REMOVE_TOOLS="no"
  fi
fi

if [ "$REMOVE_TOOLS" = "yes" ]; then
  case "$(uname -s)" in
    Darwin)
      brew uninstall --ignore-dependencies tesseract poppler pipx 2>/dev/null || true
      brew autoremove   # removes only leftover dependencies nothing else needs
      ok "Removed tesseract, poppler, pipx and unused dependencies"
      ;;
    Linux)
      sudo apt-get remove -y tesseract-ocr poppler-utils pipx
      sudo apt-get autoremove -y
      ok "Removed tesseract-ocr, poppler-utils, pipx and unused dependencies"
      ;;
  esac
else
  skip "Kept (run ./uninstall.sh --all to remove them)"
fi

step "Done!"
echo "  You can now delete this folder:  cd .. && rm -rf leantext"
