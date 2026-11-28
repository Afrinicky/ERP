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

import uvicorn

from nickland.interface.application import build_runtime, create_application
from nickland.interface.static import interface_root
from nickland.platform.config import DeploymentMode, Settings, get_settings
from nickland.platform.logging import configure_logging, get_logger

logger = get_logger(__name__)

WINDOW_TITLE = "Nickland ERP"
MINIMUM_SIZE = (1100, 700)
INITIAL_SIZE = (1440, 900)


DESKTOP_SHELL_QUERY = "shell=desktop"


class WindowBridge:
    """What the page inside the application window may ask of the host: its token.

    pywebview exposes this object's public methods to the page as
    ``window.pywebview.api``. It has one, and holds the token privately.
    """

    __slots__ = ("_token",)

    def __init__(self, token: str) -> None:
        self._token = token

    def local_token(self) -> str:
        return self._token


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

    # A new installation has no schema; an upgraded one may be behind. Either way it is
    # brought to the current revision, with a copy taken first, before anything is served.
    from nickland.persistence.engine import create_database_engine
    from nickland.persistence.schema import ensure_current

    resolved.ensure_directories()
    engine = create_database_engine(resolved)
    try:
        before, after = ensure_current(resolved, engine)
        if before != after:
            logger.info("Database schema upgraded from %s to %s", before or "nothing", after)
    finally:
        engine.dispose()

    runtime = build_runtime(resolved, local_token=token)
    application = create_application(runtime=runtime, static_root=interface_root())

    config = uvicorn.Config(
        application,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
        lifespan="on",
        ws="none",
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


def verify_host(settings: Settings) -> str:
    """The host's address, once its certificate has been shown to be the one pinned.

    A client never opens a host it cannot identify: a machine answering on the host's
    address with another certificate is refused before a password is typed into it.
    """
    from urllib.parse import urlparse

    from nickland.platform.tls import peer_fingerprint

    if not settings.host_url:
        raise RuntimeError("Set NICKLAND_HOST_URL to the host, for example https://erp-host:8443.")
    address = urlparse(settings.host_url)
    if address.scheme != "https" or not address.hostname:
        raise RuntimeError("A LAN client connects to its host over https only.")
    if settings.host_fingerprint:
        presented = peer_fingerprint(address.hostname, address.port or 443)
        expected = settings.host_fingerprint.strip().upper()
        if presented != expected:
            raise RuntimeError(
                "The host presented a certificate that is not the one this workstation "
                f"trusts.\n  Expected {expected}\n  Presented {presented}\n"
                "Do not sign in. Ask whoever looks after the host."
            )
    return settings.host_url.rstrip("/")


def run_client(settings: Settings) -> int:
    """A LAN workstation: no local service and no database, only the host's window."""
    try:
        url = verify_host(settings)
    except (RuntimeError, OSError) as problem:
        logger.error("The host could not be verified: %s", problem)
        print(f"Nickland ERP cannot open the host.\n{problem}")
        return 1
    try:
        import webview
    except ImportError:
        print(f"Open {url} in a browser on this workstation.")
        return 0
    webview.create_window(
        WINDOW_TITLE,
        url=url,
        width=INITIAL_SIZE[0],
        height=INITIAL_SIZE[1],
        min_size=MINIMUM_SIZE,
        text_select=True,
        confirm_close=True,
    )
    webview.start()
    return 0


def run() -> int:
    """Start the service and open the window."""
    settings = get_settings()
    settings.ensure_directories()
    configure_logging(
        level=settings.log_level,
        directory=settings.log_directory,
        as_json=settings.log_json,
    )
    if settings.deployment_mode is DeploymentMode.LAN_CLIENT:
        return run_client(settings)

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
        # The token reaches the interface through the window's own bridge, which only the
        # page inside this window can call: never through a URL, where history and logs
        # would record it, nor through the served page, which any local process could
        # fetch. The interface asks for it before its first request; the query string
        # only tells it that it is running in this window and should ask.
        webview.create_window(
            WINDOW_TITLE,
            url=f"{service.url}/?{DESKTOP_SHELL_QUERY}",
            width=INITIAL_SIZE[0],
            height=INITIAL_SIZE[1],
            min_size=MINIMUM_SIZE,
            text_select=True,
            confirm_close=True,
            js_api=WindowBridge(service.token),
        )
        webview.start()
        return 0
    finally:
        service.stop()
        lock.close()
        logger.info("Application closed")
