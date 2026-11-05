"""The desktop host.

Starts the local application service on the loopback interface, issues it a token no
other process holds, and presents the interface in the Windows system web view. No port
is exposed to the network.
"""

from __future__ import annotations

import secrets
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from nickland.interface.application import build_runtime, create_application
from nickland.platform.config import DeploymentMode, Settings, get_settings
from nickland.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)

WINDOW_TITLE = "Nickland ERP"
MINIMUM_SIZE = (1100, 700)
INITIAL_SIZE = (1440, 900)


@dataclass(slots=True)
class LocalService:
    """A running local service."""

    host: str
    port: int
    token: str
    server: uvicorn.Server
    thread: threading.Thread

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def reserve_port(host: str) -> int:
    """Reserve an ephemeral port, so the window knows where to connect."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def interface_root() -> Path | None:
    """The built interface, when it has been bundled alongside the package."""
    candidate = Path(__file__).resolve().parents[3] / "ui" / "dist"
    if candidate.is_dir():
        return candidate
    bundled = Path(__file__).resolve().parent / "interface"
    return bundled if bundled.is_dir() else None


def start_local_service(settings: Settings | None = None) -> LocalService:
    """Start the service on loopback with a single-use token."""
    resolved = settings or get_settings()
    if resolved.deployment_mode is DeploymentMode.LAN_CLIENT:
        raise RuntimeError(
            "A LAN client connects to the host service and does not start a local one."
        )

    host = "127.0.0.1"
    port = reserve_port(host)
    token = secrets.token_urlsafe(32)

    runtime = build_runtime(resolved, local_token=token)
    application = create_application(runtime=runtime, static_root=interface_root())

    config = uvicorn.Config(
        application,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="nickland-service", daemon=True)
    thread.start()

    logger.info("Local service listening on %s:%s", host, port)
    return LocalService(host=host, port=port, token=token, server=server, thread=thread)


def acquire_single_instance(settings: Settings) -> socket.socket | None:
    """Claim the single-instance lock.

    Two copies of the application against one SQLite database would contend for the
    write lock, so the second is refused. A bound socket is used rather than a lock
    file, because a socket is released even if the process is killed.
    """
    marker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        marker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        marker.bind(("127.0.0.1", _instance_port(settings)))
        marker.listen(1)
        return marker
    except OSError:
        marker.close()
        return None


def _instance_port(settings: Settings) -> int:
    """A stable high port derived from the data directory.

    Two installations with separate data directories may run side by side; two copies
    against the same directory may not.
    """
    import hashlib

    digest = hashlib.sha256(str(settings.data_directory).encode("utf-8")).digest()
    return 49200 + (int.from_bytes(digest[:2], "big") % 300)


def run() -> int:
    """Start the service and open the window."""
    settings = get_settings()
    settings.ensure_directories()
    configure_logging(
        level=settings.log_level,
        directory=settings.log_directory,
        as_json=settings.log_json,
    )

    lock = acquire_single_instance(settings)
    if lock is None:
        logger.error("Nickland ERP is already running for this data directory.")
        print(
            "Nickland ERP is already running. Switch to the open window rather than "
            "starting a second copy.",
        )
        return 1

    service = start_local_service(settings)

    try:
        import webview
    except ImportError:
        logger.warning(
            "The desktop window component is not installed. The service is running at %s",
            service.url,
        )
        print(
            f"The interface is available at {service.url}\n"
            "Install the desktop extra to open it in its own window:\n"
            "  pip install 'nickland[desktop]'",
        )
        try:
            service.thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            service.stop()
            lock.close()
        return 0

    try:
        window = webview.create_window(
            WINDOW_TITLE,
            url=service.url,
            width=INITIAL_SIZE[0],
            height=INITIAL_SIZE[1],
            min_size=MINIMUM_SIZE,
            text_select=True,
            confirm_close=True,
        )
        # The token reaches the interface through the document, never through a URL,
        # so it is not recorded in history or in a log.
        window.events.loaded += lambda: window.evaluate_js(
            "(function(){var m=document.querySelector('meta[name=\"nickland-local-token\"]');"
            f"if(m){{m.content={service.token!r};}}}})()"
        )
        webview.start()
        return 0
    finally:
        service.stop()
        lock.close()
        logger.info("Application closed")
