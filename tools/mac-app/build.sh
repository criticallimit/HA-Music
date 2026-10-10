#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
output="${1:-build/mac-app}"
app="$output/HA Music Playlist Sync.app"
mkdir -p "$app/Contents/MacOS" "$output/architectures"
sdk="$(xcrun --sdk macosx --show-sdk-path)"
for architecture in arm64 x86_64; do
  xcrun swiftc -swift-version 5 -sdk "$sdk" -target "$architecture-apple-macos12.0" \
    tools/mac-app/Core.swift tools/mac-app/main.swift \
    -framework AppKit -framework Security -o "$output/architectures/$architecture"
done
lipo -create "$output/architectures/arm64" "$output/architectures/x86_64" -output "$app/Contents/MacOS/HA Music Playlist Sync"
cp tools/mac-app/Info.plist "$app/Contents/Info.plist"
codesign --force --options runtime --entitlements tools/mac-app/Entitlements.plist --sign - "$app"
codesign --verify --deep --strict "$app"
lipo "$app/Contents/MacOS/HA Music Playlist Sync" -verify_arch arm64 x86_64
cp tools/mac-app/LIESMICH.txt "$output/LIESMICH.txt"
ditto -c -k --sequesterRsrc --keepParent "$app" "$output/HA-Music-Playlist-Sync.zip"
echo "Built universal Mac app: $output/HA-Music-Playlist-Sync.zip"
