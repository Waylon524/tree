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
npm run tauri -- build --bundles dmg
cd "$ROOT"

APP="$(find desktop/src-tauri/target/release/bundle -type d -name 'TREE.app' -print -quit)"
DMG="$(find desktop/src-tauri/target/release/bundle -type f -name '*.dmg' -print -quit)"
test -n "$APP" && test -n "$DMG"

codesign --verify --deep --strict --verbose=4 "$APP"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARYTOOL_KEYCHAIN_PROFILE" --wait
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
hdiutil verify "$DMG"
spctl --assess --type execute --verbose=4 "$APP"

VERSION="${TAG#v}"
FINAL="packaging/TREE_${VERSION}_macos.dmg"
cp "$DMG" "$FINAL"
shasum -a 256 "$FINAL" > "packaging/SHA256SUMS-macos.txt"

if [[ "${UPLOAD_RELEASE:-0}" == "1" ]]; then
  gh release upload "$TAG" "$FINAL" packaging/SHA256SUMS-macos.txt --clobber
fi

echo "Validated macOS release: $FINAL"
