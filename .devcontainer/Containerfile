FROM mcr.microsoft.com/devcontainers/base:2.1.9-bookworm

ENV PYTHONUNBUFFERED=True
ENV UV_LINK_MODE=copy

WORKDIR /opt

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv  /bin/uv
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uvx /bin/uvx
COPY ./.py-version ./.python-version

# Install required tools for development
RUN apt-get update && apt-get install -y iperf3 libusb-dev