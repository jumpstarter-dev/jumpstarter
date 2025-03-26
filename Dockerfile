FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:latest AS uv

FROM --platform=$BUILDPLATFORM fedora:40 AS builder
RUN dnf install -y make git && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=uv /uv /uvx /bin/

#FROM registry.access.redhat.com/ubi9/python-312:9.5 AS product
FROM fedora:40 AS product
RUN dnf install -y python3.12-pip ustreamer libusb1 && \
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
