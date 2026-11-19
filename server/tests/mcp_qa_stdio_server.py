"""Stdio FastMCP fake for the QA tests (docs/design/mcp-client/qa). Options:
--name X: reported by whoami. --crash-if FILE: exit at once if FILE exists.
--stubborn: ignore SIGTERM and stay alive after stdin closes.
QA_PIDFILE env: write the pid there."""

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

parser = argparse.ArgumentParser()
parser.add_argument("--name", default="qa-stdio")
parser.add_argument("--crash-if", default="")
parser.add_argument("--stubborn", action="store_true")
opts = parser.parse_args()

if opts.crash_if and Path(opts.crash_if).exists():
    sys.exit(1)
if os.environ.get("QA_PIDFILE"):
    Path(os.environ["QA_PIDFILE"]).write_text(str(os.getpid()))

mcp = FastMCP("qa-stdio")


@mcp.tool()
def echo(text: str) -> str:
    return text


@mcp.tool()
def pid() -> str:
    return str(os.getpid())


@mcp.tool()
def whoami() -> str:
    return str(opts.name)


@mcp.tool()
def env_vars() -> str:
    return "\n".join(f"{k}={v}" for k, v in sorted(os.environ.items()))


if __name__ == "__main__":
    if opts.stubborn:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    mcp.run()
    while opts.stubborn:
        time.sleep(1)
