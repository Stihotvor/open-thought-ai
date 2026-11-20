"""Tests for the shared MCP test helpers."""

import os
import signal
import subprocess

from mcp_fakes import assert_pid_gone


def test_unreaped_zombie_counts_as_gone() -> None:
    child = subprocess.Popen(["sleep", "30"])
    try:
        os.kill(child.pid, signal.SIGKILL)
        assert_pid_gone(child.pid)  # nobody waits on the child yet, so it is a zombie
    finally:
        child.wait()
