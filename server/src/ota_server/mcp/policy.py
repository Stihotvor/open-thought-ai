"""SafeConnectionPolicy (D2): scheme rule, SSRF host check and secret headers.

It keeps no state, so every connect and reconnect re-runs the check (F2). Each denial logs
one WARNING with the server, the host and the reason, never a header value (T12).
"""

import ipaddress
import logging
import os
from collections.abc import Mapping
from typing import NoReturn
from urllib.parse import urlsplit

from ota_server.mcp.config import McpServerConfig
from ota_server.mcp.hooks import ConnectionDenied
from ota_server.mcp.ssrf import Resolver, check_host, default_resolver

log = logging.getLogger(__name__)


def _is_loopback_literal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SafeConnectionPolicy:
    """A ConnectionPolicy for external MCP servers (T5, T6, T9)."""

    def __init__(
        self,
        allowed_hosts: frozenset[str],
        resolver: Resolver = default_resolver,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._allowed = allowed_hosts
        self._resolver = resolver
        self._environ: Mapping[str, str] = os.environ if environ is None else environ

    async def before_connect(self, config: McpServerConfig) -> dict[str, str]:
        """Check the connection and return its secret headers.

        Raise ConnectionDenied (logged) on denial.
        """
        if config.transport == "stdio":
            return {}
        parts = urlsplit(config.url)
        host = (parts.hostname or "").lower()
        reason = self._scheme_reason(parts.scheme, host)
        if reason is None:
            reason = await check_host(host, self._allowed, self._resolver)
        if reason is not None:
            self._deny(config, host, reason)
        return self._headers(config, host)

    def _scheme_reason(self, scheme: str, host: str) -> str | None:
        if scheme == "https":
            return None
        if scheme == "http" and (_is_loopback_literal(host) or host in self._allowed):
            return None
        return "http needs https"

    def _headers(self, config: McpServerConfig, host: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        for header, var in config.headers.items():
            value = self._environ.get(var)
            if not value:
                self._deny(config, host, f"missing variable {var}")
            headers[header] = value
        return headers

    @staticmethod
    def _deny(config: McpServerConfig, host: str, reason: str) -> NoReturn:
        log.warning("mcp %s: connection denied (host %s): %s", config.name, host, reason)
        raise ConnectionDenied(reason)
