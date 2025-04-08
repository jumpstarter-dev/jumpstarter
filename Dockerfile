FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:latest AS uv

FROM --platform=$BUILDPLATFORM fedora:40 AS builder
RUN dnf install -y make git && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=uv /uv /uvx /bin/

FROM fedora:40 AS product
RUN dnf install -y python3 ustreamer libusb1 yq openssl qemu-img qemu-system-x86 qemu-system-aarch64 && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

FROM builder AS wheels
ADD . /src
RUN make -C /src build

FROM product
RUN --mount=from=wheels,source=/src/dist,target=/dist \
    uv venv /jumpstarter && \
    VIRTUAL_ENV=/jumpstarter uv pip install /dist/*.whl
ENV PATH="/jumpstarter/bin:$PATH"
