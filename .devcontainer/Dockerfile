FROM debian:bookworm-slim AS builder

WORKDIR /opt

# The installer requires curl (and certificates) to download the release archive
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    curl

SHELL [ "/bin/bash", "-o", "pipefail", "-c" ]

# Download the latest installer
ADD https://astral.sh/uv/install.sh uv-installer.sh

# Run the installer then remove it
RUN sh uv-installer.sh


FROM mcr.microsoft.com/vscode/devcontainers/base:bookworm

ENV CARGO_HOME="/opt/.cargo/bin"
ENV PATH="$CARGO_HOME/:$PATH"

ENV PYTHONUNBUFFERED=True
ENV UV_LINK_MODE=copy

WORKDIR /opt

COPY --from=builder /root/.cargo/bin/uv $CARGO_HOME/uv
COPY --from=builder /root/.cargo/bin/uvx $CARGO_HOME/uvx
COPY ./.python-version ./

RUN uv python pin "$(cat .python-version)"

RUN chown -R vscode $CARGO_HOME

# Install required tools for development
RUN apt-get update && apt-get install -y iperf3 libusb-dev
