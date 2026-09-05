#!/usr/bin/env bash
# ResoNote setup: build the Rust app, create a Python venv, and drop a config.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${RESONOTE_HOME:-$HOME/.config/resonote}"
CFG_FILE="$DATA_DIR/config.json"
BIN_NAME="resonote"
INSTALL_BIN_DIR="${HOME}/.local/bin"

OLDDIR="$HOME/.resonote"
mkdir -p "$DATA_DIR"
if [ "$DATA_DIR" != "$OLDDIR" ] && [ -d "$OLDDIR" ]; then
    echo "==> Migrating existing $OLDDIR -> $DATA_DIR"
    for item in "$OLDDIR"/* "$OLDDIR"/.[!.]*; do
        [ -e "$item" ] || continue
        base="$(basename "$item")"
        if [ "$base" = "venv" ]; then
            continue  # recreated below (moving venvs breaks their shebangs)
        fi
        if [ ! -e "$DATA_DIR/$base" ]; then
            mv "$item" "$DATA_DIR/$base"
        fi
    done
fi

echo "==> Building Rust binary"
cargo build --release --manifest-path "$ROOT/Cargo.toml"

echo "==> Creating Python venv: $DATA_DIR/venv"
if [ ! -x "$DATA_DIR/venv/bin/python" ]; then
    python3 -m venv "$DATA_DIR/venv"
fi
"$DATA_DIR/venv/bin/python" -m pip install --upgrade pip --quiet 2>/dev/null || true
"$DATA_DIR/venv/bin/python" -m pip install -r "$ROOT/python/requirements.txt"

echo "==> Writing config (keep existing if present)"
if [ ! -f "$CFG_FILE" ]; then
    cp "$ROOT/resonote.config.example.json" "$CFG_FILE"
fi
# point "python" at the venv interpreter (unless the user chose another one)
python3 - "$CFG_FILE" "$DATA_DIR/venv/bin/python" "$OLDDIR/venv/bin/python" <<'PY'
import json, sys
path, pypath, stale = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(path))
if not cfg.get("python") or cfg.get("python") == stale:
    cfg["python"] = pypath
json.dump(cfg, open(path, "w"), indent=2)
PY

echo "==> Installing binary to $INSTALL_BIN_DIR"
mkdir -p "$INSTALL_BIN_DIR"
cp "$ROOT/target/release/$BIN_NAME" "$INSTALL_BIN_DIR/$BIN_NAME"

# The old venv was deliberately not moved (stale shebangs); clean up leftovers now
# that a fresh venv is in place at $DATA_DIR.
if [ "$DATA_DIR" != "$OLDDIR" ] && [ -d "$OLDDIR" ]; then
    echo "==> Removing leftover $OLDDIR (migrated/rebuilt in $DATA_DIR)"
    rm -rf -- "$OLDDIR"
fi

echo
echo "Setup done."
echo
echo "  1. Add your FREE API keys to:  $CFG_FILE"
echo "     - groq_api_key    -> https://console.groq.com          (speech-to-text)"
echo "     - gemini_api_key  -> https://aistudio.google.com/apikey (notes LLM)"
echo
echo "  2. Optional (Wayland/Hyprland keybind instead of the X11 hotkey):"
echo "     bind = SUPER, SHIFT, J, exec, $INSTALL_BIN_DIR/$BIN_NAME toggle"
echo
echo "  3. Run:  $INSTALL_BIN_DIR/$BIN_NAME"
echo "     Notes land in ~/VoiceNotes/"