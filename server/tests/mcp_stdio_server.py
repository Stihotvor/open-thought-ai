"""A stdio FastMCP fake server for the adapter tests. The tests run it as a child process."""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stdio-fake")


@mcp.tool()
def echo(text: str) -> str:
    return text


@mcp.tool()
def env_dump() -> str:
    """The names of the child's environment variables, one per line."""
    return "\n".join(sorted(os.environ))


@mcp.tool()
def pid() -> str:
    return str(os.getpid())


if __name__ == "__main__":
    mcp.run()
