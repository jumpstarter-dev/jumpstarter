# Build the manager binary
FROM registry.access.redhat.com/ubi9/go-toolset:1.24.6 AS builder
ARG TARGETOS
ARG TARGETARCH
ARG GIT_VERSION=unknown
ARG GIT_COMMIT=unknown
ARG BUILD_DATE=unknown

# Copy the Go Modules manifests
COPY go.mod go.mod
COPY go.sum go.sum
# cache deps before building and copying source so that we don't need to re-download as much
# and so that source changes don't invalidate our downloaded layer
# Cache module downloads across builds
RUN --mount=type=cache,target=/opt/app-root/src/go/pkg/mod,sharing=locked,uid=1001,gid=0 \
        --mount=type=cache,target=/opt/app-root/src/.cache/go-build,sharing=locked,uid=1001,gid=0 \
        go mod download

# Copy the go source
COPY cmd/ cmd/
COPY api/ api/
COPY internal/ internal/

# Build
# the GOARCH has not a default value to allow the binary be built according to the host where the command
# was called. For example, if we call make docker-build in a local env which has the Apple Silicon M1 SO
# the docker BUILDPLATFORM arg will be linux/arm64 when for Apple x86 it will be linux/amd64. Therefore,
# by leaving it empty we can ensure that the container and binary shipped on it will have the same platform.
RUN --mount=type=cache,target=/opt/app-root/src/go/pkg/mod,sharing=locked,uid=1001,gid=0 \
--mount=type=cache,target=/opt/app-root/src/.cache/go-build,sharing=locked,uid=1001,gid=0 \
    CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH} \
    go build -a \
    -ldflags "-X main.version=${GIT_VERSION} -X main.gitCommit=${GIT_COMMIT} -X main.buildDate=${BUILD_DATE}" \
    -o manager cmd/main.go
RUN  --mount=type=cache,target=/opt/app-root/src/go/pkg/mod,sharing=locked,uid=1001,gid=0 \
     --mount=type=cache,target=/opt/app-root/src/.cache/go-build,sharing=locked,uid=1001,gid=0 \
     CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH} \
     go build -a \
     -ldflags "-X main.version=${GIT_VERSION} -X main.gitCommit=${GIT_COMMIT} -X main.buildDate=${BUILD_DATE}" \
     -o router  cmd/router/main.go

FROM registry.access.redhat.com/ubi9/ubi-micro:9.5
WORKDIR /
COPY --from=builder /opt/app-root/src/manager .
COPY --from=builder /opt/app-root/src/router  .
USER 65532:65532

ENTRYPOINT ["/manager"]
