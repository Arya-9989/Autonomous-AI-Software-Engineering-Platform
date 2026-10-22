# syntax=docker/dockerfile:1.7
# The sandbox image: the fixed environment a generated project's lint, type check and tests run
# in. Containers from it get no network, so everything a generated project may import is
# installed here, from a hash-locked requirements file. Build from the repository root:
#   docker build -f sandbox/Dockerfile -t autoswe-sandbox:local .
FROM python:3.12.15-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY sandbox/requirements.txt /tmp/requirements.txt
# --require-hashes: every wheel is verified against the lock. The root file system is read-only
# at run time, so byte code is compiled now rather than written on first import.
RUN pip install --no-deps --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && python -m compileall -q /usr/local/lib/python3.12 \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
    && groupadd --system --gid 10002 sandbox \
    && useradd --system --uid 10002 --gid sandbox --home-dir /tmp --shell /usr/sbin/nologin sandbox

COPY src/autoswe/sandbox/inside/ /opt/autoswe-sandbox/
RUN python -m compileall -q /opt/autoswe-sandbox

USER 10002:10002
WORKDIR /workspace
CMD ["python", "/opt/autoswe-sandbox/entry.py", "--help"]
