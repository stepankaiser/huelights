#!/bin/zsh
# Installs HueLights: a background app that runs lightd.py at login.
#   ./install.sh            install or update (keeps your edited config.json)
#   ./install.sh uninstall  stop it and remove everything
set -euo pipefail

SRC="${0:A:h}"
APP="$HOME/Applications/HueLights.app"
SUPPORT="$HOME/Library/Application Support/HueLights"
AGENT="$HOME/Library/LaunchAgents/local.huelights.plist"
LABEL="local.huelights"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true

if [[ "${1:-}" == "uninstall" ]]; then
  rm -rf "$APP" "$SUPPORT" "$AGENT"
  echo "HueLights removed (log left at ~/Library/Logs/HueLights.log)."
  exit 0
fi

mkdir -p "$SUPPORT" "$APP/Contents/MacOS" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

# code + helpers (your config.json and saved light are kept on reinstall)
cp "$SRC/lightd.py" "$SRC/hue.py" "$SRC/brand.py" "$SUPPORT/"
[[ -f "$SUPPORT/config.json" ]] || cp "$SRC/config.example.json" "$SUPPORT/config.json"
clang -O2 -o "$SUPPORT/callstate" "$SRC/native/callstate.c" -framework CoreAudio -framework CoreMediaIO -framework CoreFoundation
clang -O2 -o "$APP/Contents/MacOS/HueLights" "$SRC/native/launcher.c"

# python environment
[[ -x "$SUPPORT/venv/bin/python" ]] || python3 -m venv "$SUPPORT/venv"
"$SUPPORT/venv/bin/pip" -q install --upgrade -r "$SRC/requirements.txt"

# the app bundle exists so macOS has somewhere to read the Bluetooth/Calendar usage strings from
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>$LABEL</string>
  <key>CFBundleName</key><string>HueLights</string>
  <key>CFBundleExecutable</key><string>HueLights</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSBluetoothAlwaysUsageDescription</key><string>HueLights controls your Hue light over Bluetooth.</string>
  <key>NSCalendarsFullAccessUsageDescription</key><string>HueLights reads your meetings to colour the light for the company you are talking to.</string>
  <key>NSCalendarsUsageDescription</key><string>HueLights reads your meetings to colour the light for the company you are talking to.</string>
</dict></plist>
EOF
codesign --force --sign - "$APP"

cat > "$AGENT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/Contents/MacOS/HueLights</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/HueLights.stdout.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/HueLights.stdout.log</string>
</dict></plist>
EOF

launchctl bootstrap "gui/$UID" "$AGENT"
echo "HueLights installed and running. Log: ~/Library/Logs/HueLights.log"
echo "Config: $SUPPORT/config.json  (edits are picked up within 2 minutes)"
echo "First run: allow Bluetooth and Calendar access when macOS asks."
