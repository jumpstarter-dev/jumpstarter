FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:latest AS uv

FROM --platform=$BUILDPLATFORM fedora:43 AS builder
RUN dnf install -y make git && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=uv /uv /uvx /bin/

FROM fedora:43 AS product
RUN dnf install -y python3 ustreamer libusb1 android-tools python3-libgpiod curl && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ARG FLS_VERSION=0.2.0
RUN ARCH=$(uname -m) && \
    URL="https://github.com/jumpstarter-dev/fls/releases/download/${FLS_VERSION}/fls-${ARCH}-linux" && \
    TEMP_FILE="/tmp/fls-${ARCH}-linux.tmp" && \
    curl -fsSL "${URL}" -o "${TEMP_FILE}" && \
    mv "${TEMP_FILE}" /usr/local/bin/fls && \
    chmod +x /usr/local/bin/fls

FROM builder AS wheels
ADD . /src
RUN make -C /src/python build

FROM product
RUN --mount=from=wheels,source=/src/python/dist,target=/dist \
    uv venv /jumpstarter && \
    VIRTUAL_ENV=/jumpstarter uv pip install /dist/*.whl
ENV PATH="/jumpstarter/bin:$PATH"
