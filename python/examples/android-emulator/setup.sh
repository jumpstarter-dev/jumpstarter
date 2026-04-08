#!/bin/bash
# Setup script for Android emulator testing example.
# Locates the Android SDK, installs a system image, and creates a test AVD.
#
# Usage: source setup.sh
#
# This script should be sourced (not executed) so that PATH and
# environment variables are set in the current shell.

set -e

AVD_NAME="${ANDROID_AVD_NAME:-jumpstarter_test}"
DEVICE="pixel_6"

# --- Locate Android SDK ---
if [ -z "$ANDROID_HOME" ]; then
    # Common default locations
    for candidate in \
        "$HOME/Library/Android/sdk" \
        "$HOME/Android/Sdk" \
        "/opt/android-sdk"; do
        if [ -d "$candidate" ]; then
            export ANDROID_HOME="$candidate"
            break
        fi
    done
fi

if [ -z "$ANDROID_HOME" ] || [ ! -d "$ANDROID_HOME" ]; then
    echo "ERROR: Android SDK not found."
    echo "  Install Android Studio or set ANDROID_HOME to your SDK path."
    return 1 2>/dev/null || exit 1
fi

echo "Android SDK: $ANDROID_HOME"

# --- Add tools to PATH ---
export PATH="$ANDROID_HOME/emulator:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

# --- Verify required tools ---
for tool in emulator adb sdkmanager avdmanager; do
    if ! command -v "$tool" &>/dev/null; then
        echo "ERROR: '$tool' not found in PATH."
        echo "  Ensure Android SDK command-line tools are installed."
        return 1 2>/dev/null || exit 1
    fi
done

# --- Detect architecture ---
ARCH=$(uname -m)
case "$ARCH" in
    arm64|aarch64)
        SYS_IMAGE_ARCH="arm64-v8a"
        ;;
    x86_64|amd64)
        SYS_IMAGE_ARCH="x86_64"
        ;;
    *)
        echo "ERROR: Unsupported architecture: $ARCH"
        return 1 2>/dev/null || exit 1
        ;;
esac

SYS_IMAGE="system-images;android-35;google_apis;${SYS_IMAGE_ARCH}"
echo "Architecture: $ARCH -> $SYS_IMAGE_ARCH"
echo "System image: $SYS_IMAGE"

# --- Install system image if missing ---
SYS_IMAGE_PATH="$ANDROID_HOME/system-images/android-35/google_apis/${SYS_IMAGE_ARCH}"
if [ ! -d "$SYS_IMAGE_PATH" ]; then
    echo "Installing system image (this may take a few minutes)..."
    yes | sdkmanager "$SYS_IMAGE"
else
    echo "System image already installed at $SYS_IMAGE_PATH"
fi

# --- Create AVD if missing ---
if ! avdmanager list avd 2>/dev/null | grep -q "Name: $AVD_NAME"; then
    echo "Creating AVD: $AVD_NAME (device: $DEVICE)..."
    echo "no" | avdmanager create avd \
        -n "$AVD_NAME" \
        -k "$SYS_IMAGE" \
        -d "$DEVICE" \
        --force
else
    echo "AVD '$AVD_NAME' already exists."
fi

# --- Export for tests ---
export ANDROID_AVD_NAME="$AVD_NAME"

echo ""
echo "Setup complete. To run the tests:"
echo "  pytest jumpstarter_example_android_emulator/ -v"
echo ""
echo "Environment:"
echo "  ANDROID_HOME=$ANDROID_HOME"
echo "  ANDROID_AVD_NAME=$ANDROID_AVD_NAME"
