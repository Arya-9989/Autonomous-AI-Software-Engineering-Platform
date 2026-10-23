# syntax=docker/dockerfile:1.7
# The TypeScript sandbox image: Node.js 24 to run the tests (Node strips the types itself), the
# pinned type checker and linter from package-lock.json, and the same Python entry script as the
# other sandboxes. Containers get no network, so nothing is installed at run time.
#   docker build -f sandbox/node/Dockerfile -t autoswe-sandbox-node:local .
FROM node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS tools
WORKDIR /opt/autoswe-sandbox/node
COPY sandbox/node/package.json sandbox/node/package-lock.json ./
RUN npm ci --no-audit --no-fund --ignore-scripts && npm cache clean --force

FROM python:3.12.15-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTOSWE_NODE_MODULES=/opt/autoswe-sandbox/node/node_modules

COPY --from=tools /usr/local/bin/node /usr/local/bin/node
COPY --from=tools /opt/autoswe-sandbox/node/node_modules /opt/autoswe-sandbox/node/node_modules
COPY src/autoswe/sandbox/inside/ /opt/autoswe-sandbox/
COPY sandbox/node/eslint.config.mjs /opt/autoswe-sandbox/node/eslint.config.mjs
RUN python -m compileall -q /opt/autoswe-sandbox \
    && node --version \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
    && groupadd --system --gid 10002 sandbox \
    && useradd --system --uid 10002 --gid sandbox --home-dir /tmp --shell /usr/sbin/nologin sandbox

USER 10002:10002
WORKDIR /workspace
CMD ["python", "/opt/autoswe-sandbox/entry.py", "--help"]
