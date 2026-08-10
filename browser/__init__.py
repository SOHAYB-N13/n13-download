"""Browser integration (protocol handler + live server)."""

from browser.live_server import run_live_server
from browser.protocol import (
    browser_integration_setup,
    create_chrome_extension,
    is_protocol_registered,
    register_protocol,
    unregister_protocol,
)

__all__ = [
    "run_live_server",
    "browser_integration_setup",
    "create_chrome_extension",
    "is_protocol_registered",
    "register_protocol",
    "unregister_protocol",
]
