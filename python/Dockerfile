FROM fedora:40 as builder
RUN dnf install -y make git && \
    dnf clean all && \
    rm -rf /var/cache/dnf
COPY --from=ghcr.io/astral-sh/uv:latest /uv  /bin/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uvx /bin/uvx
ADD . /src
RUN make -C /src build

FROM fedora:40
RUN dnf install -y python3-pip && \
    dnf clean all && \
    rm -rf /var/cache/dnf
RUN --mount=from=builder,source=/src/dist,target=/dist pip install /dist/*.whl
