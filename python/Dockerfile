FROM fedora:40 AS builder
RUN dnf install -y make git && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ADD . /src
RUN make -C /src build

FROM fedora:40
RUN dnf install -y python3 ustreamer libusb1 && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN --mount=from=builder,source=/src/dist,target=/dist \
    uv venv /jumpstarter && \
    VIRTUAL_ENV=/jumpstarter uv pip install /dist/*.whl
ENV PATH="/jumpstarter/bin:$PATH"
