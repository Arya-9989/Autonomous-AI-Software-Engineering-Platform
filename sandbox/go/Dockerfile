# syntax=docker/dockerfile:1.7
# The Go sandbox image: the Go 1.27 toolchain, a build cache already holding the standard-library
# packages generated projects use (compiled from seed/ at image build; the entry copies it into
# the run's /tmp), and the same Python entry script as the other sandboxes. Containers get no
# network and no module proxy (GOPROXY=off), so generated projects use the standard library only.
#   docker build -f sandbox/go/Dockerfile -t autoswe-sandbox-go:local .
FROM golang:1.27.3-bookworm@sha256:972b4110ba0f47539a975c31e52c64765e1ff19f2d74469ae4a595c6676e80a5 AS toolchain
ENV GOCACHE=/opt/autoswe-sandbox/go/cache \
    GOFLAGS=-mod=mod \
    GOPROXY=off \
    GOTOOLCHAIN=local \
    GOTELEMETRY=off \
    CGO_ENABLED=0
WORKDIR /seed
COPY sandbox/go/seed/ ./
RUN go vet ./... && go test ./... && gofmt -l . && rm -rf /root/go

FROM python:3.12.15-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/usr/local/go/bin:$PATH \
    AUTOSWE_GO_CACHE_SEED=/opt/autoswe-sandbox/go/cache

COPY --from=toolchain /usr/local/go /usr/local/go
COPY --from=toolchain /opt/autoswe-sandbox/go/cache /opt/autoswe-sandbox/go/cache
COPY src/autoswe/sandbox/inside/ /opt/autoswe-sandbox/
RUN python -m compileall -q /opt/autoswe-sandbox \
    && go version \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
    && groupadd --system --gid 10002 sandbox \
    && useradd --system --uid 10002 --gid sandbox --home-dir /tmp --shell /usr/sbin/nologin sandbox

USER 10002:10002
WORKDIR /workspace
CMD ["python", "/opt/autoswe-sandbox/entry.py", "--help"]
