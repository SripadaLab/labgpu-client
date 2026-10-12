#!/bin/bash
# Installs (or updates) labgpu for the current user, Mac or Linux. Everything
# goes in ~/.labgpu; no admin password, Homebrew or AWS CLI needed.
#   curl -fsSL <base>/install.sh | bash
set -euo pipefail
BASE=${LABGPU_BASE:-https://raw.githubusercontent.com/SripadaLab/labgpu-client/main}
DIR=$HOME/.labgpu
PLUGIN_URL=https://s3.amazonaws.com/session-manager-downloads/plugin/latest

say() { printf '%s\n' "$*"; }
mkdir -p "$DIR/bin" && chmod 700 "$DIR"

# 1. Python 3.9+ (macOS: comes with Apple's Command Line Tools)
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  if [ "$(uname -s)" = Darwin ]; then
    xcode-select --install 2>/dev/null || true
    say "STOP: Python 3 isn't installed yet. A window titled 'Install Command Line Developer Tools' should have"
    say "opened: the person clicks Install (about 5 minutes), then run this installer again."
  else
    say "STOP: Python 3.9 or newer is needed (for example: sudo apt install python3 python3-venv)."
  fi
  exit 1
fi

# 2. A private Python environment with the two libraries labgpu uses
[ -x "$DIR/venv/bin/python" ] || python3 -m venv "$DIR/venv"
"$DIR/venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade boto3 segno

# 3. labgpu itself
curl -fsSL "$BASE/labgpu.py" -o "$DIR/labgpu.py.new" && mv "$DIR/labgpu.py.new" "$DIR/labgpu.py"
curl -fsSL "$BASE/config.json" -o "$DIR/config.json.new" && mv "$DIR/config.json.new" "$DIR/config.json"
cat > "$DIR/bin/labgpu" <<EOF
#!/bin/sh
exec "$DIR/venv/bin/python" "$DIR/labgpu.py" "\$@"
EOF
chmod 755 "$DIR/bin/labgpu"

# 4. AWS's Session Manager plugin, unpacked into ~/.labgpu (SSH travels through it)
if [ ! -x "$DIR/bin/session-manager-plugin" ]; then
  tmp=$(mktemp -d)
  case "$(uname -s)/$(uname -m)" in
    Darwin/arm64)  curl -fsSL "$PLUGIN_URL/mac_arm64/sessionmanager-bundle.zip" -o "$tmp/p.zip" ;;
    Darwin/x86_64) curl -fsSL "$PLUGIN_URL/mac/sessionmanager-bundle.zip" -o "$tmp/p.zip" ;;
    Linux/x86_64)  curl -fsSL "$PLUGIN_URL/ubuntu_64bit/session-manager-plugin.deb" -o "$tmp/p.deb" ;;
    Linux/aarch64) curl -fsSL "$PLUGIN_URL/ubuntu_arm64/session-manager-plugin.deb" -o "$tmp/p.deb" ;;
    *) say "STOP: this computer type ($(uname -s)/$(uname -m)) isn't supported. Ask the lab admin."; exit 1 ;;
  esac
  if [ -f "$tmp/p.zip" ]; then
    "$DIR/venv/bin/python" -m zipfile -e "$tmp/p.zip" "$tmp"
    cp "$tmp/sessionmanager-bundle/bin/session-manager-plugin" "$DIR/bin/"
  else
    (cd "$tmp" && ar x p.deb && tar xf data.tar.*)
    cp "$tmp/usr/local/sessionmanagerplugin/bin/session-manager-plugin" "$DIR/bin/"
  fi
  chmod 755 "$DIR/bin/session-manager-plugin"
  rm -rf "$tmp"
fi

# 5. SSH key, SSH host "labgpu", AWS profile "labgpu"
"$DIR/bin/labgpu" setup

# 6. Put labgpu on the PATH for new terminals
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  if [ -f "$rc" ] || [ "$rc" = "$HOME/.zshrc" -a "$(uname -s)" = Darwin ]; then
    grep -qs '.labgpu/bin' "$rc" || printf '\nexport PATH="$HOME/.labgpu/bin:$PATH"  # labgpu\n' >> "$rc"
  fi
done

say "labgpu is installed at $DIR/bin/labgpu"
say "Next: $DIR/bin/labgpu login   (opens a sign-in page in the browser for the person to finish)"
