#!/usr/bin/env bash
# Download Appium's public sample apps into mobile/apps/, checking their SHA256.
#
#   ApiDemos-debug.apk               https://github.com/appium/android-apidemos/releases
#   UIKitCatalog-iphonesimulator.zip https://github.com/appium/ios-uicatalog/releases
#
# Run it yourself; the test suites never download anything and skip the
# sample app tests while the files are missing.
#
# MAINTAINERS: the releases and checksums below are pinned by hand. To move to
# a newer release, download the file once and update its URL and
# `shasum -a 256 <file>` together.
set -euo pipefail

APIDEMOS_URL="https://github.com/appium/android-apidemos/releases/download/v6.0.18/ApiDemos-debug.apk"
APIDEMOS_SHA256="a9eecf37b26cd084855c530db81c2bb1b91f4c1b095a04f47aa7c20e2791f686"

UIKITCATALOG_URL="https://github.com/appium/ios-uicatalog/releases/download/v4.0.5/UIKitCatalog-iphonesimulator.zip"
UIKITCATALOG_SHA256="6708c9cbdf28bf69b45b10b2cfd53e98c998ad011315bb21b1815399f6a8387e"

APPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../apps" && pwd)"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

fetch() {
  local url="$1" expected="$2" name
  name="$(basename "$url")"
  if [[ -f "$APPS_DIR/$name" && "$(sha256 "$APPS_DIR/$name")" == "$expected" ]]; then
    echo "$name: already present"
    return 0
  fi
  local partial="$APPS_DIR/.$name.partial"
  echo "$name: downloading $url"
  curl --fail --location --silent --show-error --output "$partial" "$url"
  local actual
  actual="$(sha256 "$partial")"
  if [[ "$actual" != "$expected" ]]; then
    rm -f "$partial"
    echo "error: $name has SHA256 $actual, expected $expected" >&2
    return 1
  fi
  mv "$partial" "$APPS_DIR/$name"
  echo "$name: ok"
}

fetch "$APIDEMOS_URL" "$APIDEMOS_SHA256"
fetch "$UIKITCATALOG_URL" "$UIKITCATALOG_SHA256"
