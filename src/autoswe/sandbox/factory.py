"""Build the configured sandbox backend."""

from __future__ import annotations

import logging

from autoswe.config import SandboxSettings
from autoswe.sandbox.backends import DockerSandbox, LocalProcessSandbox, RemoteSandbox, Sandbox

logger = logging.getLogger(__name__)


def build_sandbox(settings: SandboxSettings) -> Sandbox | None:
    limits = settings.sandbox_limits()
    match settings.sandbox_backend:
        case "none":
            return None
        case "remote":
            assert settings.sandbox_runner_url is not None
            assert settings.sandbox_runner_token is not None
            return RemoteSandbox(
                settings.sandbox_runner_url,
                settings.sandbox_runner_token.get_secret_value(),
                timeout_s=settings.sandbox_timeout_s,
            )
        case "docker":
            logger.warning(
                "SANDBOX_BACKEND=docker: this process can start containers, which is "
                "root-equivalent on the host. Deployments should use the sandbox runner "
                "(SANDBOX_BACKEND=remote); see docs/adr/0001."
            )
            return DockerSandbox(
                settings.sandbox_image,
                limits,
                runtime=settings.sandbox_docker_runtime,
                docker=settings.sandbox_docker_binary,
                pool=settings.sandbox_pool,
                max_concurrent=settings.sandbox_max_concurrent_runs,
            )
        case "local":
            logger.warning(
                "SANDBOX_BACKEND=local runs generated code as a plain subprocess with no "
                "network or file-system isolation. Use it for development only."
            )
            return LocalProcessSandbox(limits)
