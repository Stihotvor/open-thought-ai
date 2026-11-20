"""A raw stdio JSON-RPC server that answers every tools/call with a malformed result (QA-028)."""

import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    result: dict[str, object] | None = None
    if method == "initialize":
        result = {
            "protocolVersion": msg["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "raw", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": "boom", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        result = {"content": "this is not a list"}
    if result is not None and mid is not None:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
        sys.stdout.flush()
