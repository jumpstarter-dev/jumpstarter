#!/bin/sh
set -e

VERSION=$1

# Install the jumpstarter binary

# Determine arch into amd64 or arm64
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
    ARCH="amd64"
elif [ "$ARCH" = "aarch64" ]; then
    ARCH="arm64"
else
    echo "Unsupported architecture: $ARCH"
    exit 1
fi

cd /tmp
curl -L https://github.com/jumpstarter-dev/jumpstarter/releases/download/${VERSION}/jumpstarter-${VERSION}-linux-${ARCH}.tar.gz | tar xvfz - jumpstarter
mv jumpstarter /usr/local/bin
