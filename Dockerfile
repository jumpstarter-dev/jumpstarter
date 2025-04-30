FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:latest AS uv

FROM --platform=$BUILDPLATFORM fedora:40 AS builder
RUN dnf install -y make git && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=uv /uv /uvx /bin/

FROM fedora:40 AS product
RUN dnf install -y python3 ustreamer libusb1 && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

FROM builder AS wheels
ADD . /src
WORKDIR /src
RUN make sync
# remove the package dependency pinning for jumpstarter related packages
RUN uv run ./scripts/pin_release_versions.py --unpin
RUN make build

FROM product
RUN --mount=from=wheels,source=/src/dist,target=/dist \
    uv venv /jumpstarter && \
    VIRTUAL_ENV=/jumpstarter uv pip install /dist/*.whl
ENV PATH="/jumpstarter/bin:$PATH"
