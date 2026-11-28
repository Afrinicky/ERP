"""Run the application service.

Used by the desktop host, and directly for development and for the LAN host service.
"""

from __future__ import annotations

import argparse
import socket

import uvicorn

from nickland.interface.application import create_application
from nickland.platform.config import DeploymentMode, get_settings


def free_port(host: str) -> int:
    """Reserve an ephemeral port, so the host process knows where to connect."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true", help="development only")
    arguments = parser.parse_args()

    settings = get_settings()
    host = arguments.host or settings.service_host
    port = arguments.port or settings.service_port or free_port(host)

    if settings.deployment_mode is DeploymentMode.STANDALONE and host != "127.0.0.1":
        parser.error(
            "A standalone deployment binds to 127.0.0.1 only. "
            "Set NICKLAND_DEPLOYMENT_MODE=lan_host to serve clients."
        )

    uvicorn.run(
        "nickland.interface.application:create_application"
        if arguments.reload
        else create_application(settings),
        factory=arguments.reload,
        host=host,
        port=port,
        reload=arguments.reload,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
