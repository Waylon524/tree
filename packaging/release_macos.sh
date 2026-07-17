#!/usr/bin/env bash
# Build, sign, notarize, staple, verify, and optionally upload the macOS DMG.
# Credentials are read by notarytool from the named Keychain profile only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

: "${APPLE_SIGNING_IDENTITY:?Set APPLE_SIGNING_IDENTITY to a Developer ID Application identity}"
: "${NOTARYTOOL_KEYCHAIN_PROFILE:?Set NOTARYTOOL_KEYCHAIN_PROFILE to the notarytool Keychain profile name}"

TAG="$(git describe --tags --exact-match HEAD)"
python3 packaging/release_doctor.py --tag "$TAG"

python3 -m pip install -c packaging/release-constraints.txt -e ".[rag,gui]" pyinstaller==6.21.0
python3 -m PyInstaller --noconfirm --clean \
  --distpath packaging/dist --workpath packaging/build packaging/tre-engine.spec
packaging/dist/tre-engine/tre-engine doctor --strict

cd desktop
npm ci
npm test
npm run build
cargo fmt --check --manifest-path src-tauri/Cargo.toml
cargo test --locked --manifest-path src-tauri/Cargo.toml
# Tauri auto-notarizes the App bundle when Apple API credentials are present in
# the ambient shell. This release flow intentionally signs the App, places it
# in the DMG, and submits only that DMG to notarytool below.
env \
  -u APPLE_API_ISSUER \
  -u APPLE_API_KEY \
  -u APPLE_API_KEY_PATH \
  -u APPLE_ID \
  -u APPLE_PASSWORD \
  -u APPLE_TEAM_ID \
  npm run tauri -- build --bundles dmg
cd "$ROOT"

DMG="$(find desktop/src-tauri/target/release/bundle -type f -name '*.dmg' -print -quit)"
test -n "$DMG"

codesign --verify --strict --verbose=4 "$DMG"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARYTOOL_KEYCHAIN_PROFILE" --wait
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
hdiutil verify "$DMG"

MOUNT_DIR="$(mktemp -d)"
MOUNTED=0
cleanup_mount() {
  if [[ "$MOUNTED" == "1" ]]; then
    hdiutil detach "$MOUNT_DIR" >/dev/null
  fi
  rmdir "$MOUNT_DIR" 2>/dev/null || true
}
trap cleanup_mount EXIT
hdiutil attach -readonly -nobrowse -mountpoint "$MOUNT_DIR" "$DMG" >/dev/null
MOUNTED=1
BUNDLED_APP="$(find "$MOUNT_DIR" -maxdepth 1 -type d -name 'TREE.app' -print -quit)"
test -n "$BUNDLED_APP"
codesign --verify --deep --strict --verbose=4 "$BUNDLED_APP"
spctl --assess --type execute --verbose=4 "$BUNDLED_APP"
hdiutil detach "$MOUNT_DIR" >/dev/null
MOUNTED=0
rmdir "$MOUNT_DIR"
trap - EXIT

VERSION="${TAG#v}"
FINAL="packaging/TREE_${VERSION}_macos.dmg"
cp "$DMG" "$FINAL"
shasum -a 256 "$FINAL" > "packaging/SHA256SUMS-macos.txt"

if [[ "${UPLOAD_RELEASE:-0}" == "1" ]]; then
  gh release upload "$TAG" "$FINAL" packaging/SHA256SUMS-macos.txt --clobber
fi

echo "Validated macOS release: $FINAL"
