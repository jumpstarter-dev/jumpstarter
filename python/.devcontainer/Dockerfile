FROM mcr.microsoft.com/vscode/devcontainers/base:bookworm

ENV PYTHONUNBUFFERED=True
ENV UV_LINK_MODE=copy

WORKDIR /opt

COPY --from=ghcr.io/astral-sh/uv:latest /uv  /bin/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uvx /bin/uvx
COPY ./.python-version ./

RUN uv python pin "$(cat .python-version)"

# Install required tools for development
RUN apt-get update && apt-get install -y iperf3 libusb-dev