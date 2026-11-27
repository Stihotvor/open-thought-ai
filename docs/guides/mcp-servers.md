# MCP servers: n8n example

Any MCP server connects to the hub in the same way: one entry in the servers file.
This guide uses an n8n workflow as the worked example.

The hub has no n8n logic. It speaks MCP over streamable HTTP (`"transport": "http"`) or SSE (`"transport": "sse"`).
An n8n MCP Server Trigger supports both.
This guide covers remote servers (`http` or `sse`) only. The hub also supports local `stdio` servers, but this guide does not cover them.

## Before you start
- A running open-thought-ai server (Docker Compose).
- An n8n instance that the server host can reach.
- Access to the `.env` file of the server.

## Setup steps

1. In n8n, create a workflow and add an MCP Server Trigger. Attach each tool through the Custom n8n Workflow Tool node. Pick the auth: none, bearer, header or OAuth2. Copy the trigger URL. The node type id, the URL path and the workflow JSON shape differ between n8n versions, so see your n8n UI.
2. Write the servers file `mcp-servers.json` next to `compose.yaml`:
   - Copy the example below.
   - Put the trigger URL in `url`.
   - Write each tool name of the workflow as a key of `tools`.
   - Keep the dummy pins for now.
   - Mount the servers file read-only into the container. For example, add `./mcp-servers.json:/config/mcp-servers.json:ro` under `services.server.volumes` in a `compose.override.yaml`.
3. Set three lines in the `.env` file: `OTA_MCP_SERVERS_FILE`, `OTA_MCP_ALLOWED_HOSTS` and `MCP_SECRET_N8N`. See the block below.
4. Start the hub with `docker compose up --detach server`. Read `docker compose logs server`. For each tool, find the WARNING line `mcp n8n: tool '<name>' not admitted, current hash sha256:...`.
5. Before you pin a tool, read its description and input schema in the n8n UI. The hub never logs the description. Then copy the `sha256:...` value of each tool into `tools`, as the pin of that tool.
6. Restart the server with `docker compose up --detach server`. Ask for the tool by voice. A tool that is not in `no_confirm` asks you to confirm. Say yes, and the tool runs.

The servers file for step 2:

```json
[
  {
    "name": "n8n",
    "transport": "http",
    "url": "https://n8n.example.com/<path from your n8n UI>",
    "headers": {"Authorization": "MCP_SECRET_N8N"},
    "timeout": 10,
    "tools": {
      "get_weather": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      "turn_on_heater": "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    },
    "no_confirm": ["get_weather"]
  }
]
```

The URL path is a placeholder. The real path comes from the trigger node, so see your n8n UI.
The tool names are examples. Use the names of your own workflow tools.

The rules of the servers file:
- `name` becomes the tool namespace. Do not use `device`.
- `headers` maps a header name to the name of an `MCP_SECRET_*` variable. The servers file never holds the secret.
- `tools` is an allowlist. The hub admits a tool only when its pin matches the current hash.
- `no_confirm` lists tools that run without the spoken yes. Each tool in `no_confirm` must be a key of `tools`.
- Put a tool in `no_confirm` only when it is read-only, has no side effects and returns trusted data.
- `timeout` is 1 to 60 seconds. The default is 10.
- The URL must not hold user info or a query string.

The `.env` lines for step 3:

```bash
OTA_MCP_SERVERS_FILE=/config/mcp-servers.json
# Only for an n8n host on a LAN or private address. Use the exact host of the url.
OTA_MCP_ALLOWED_HOSTS=n8n.example.com
# The full header value: for bearer auth, write the word Bearer and the token
MCP_SECRET_N8N=Bearer <token from n8n>
```

`OTA_MCP_ALLOWED_HOSTS` is a comma-separated list of hosts.
An allowlisted host skips the SSRF check. It also accepts plain `http`.
Use `https` when you can.
Allowlist only the exact n8n host. An IP literal is safer than a DNS name, because the hub does not check the address of an allowlisted name.
If you use an IP literal, put the same IP literal in `url`.

The hub sends the value of `MCP_SECRET_N8N` as the `Authorization` header.
With header auth, put the header name of n8n as the key in `headers`.
Use bearer or header auth.
Auth "none" is only for a loopback or isolated network. Never use auth "none" for tools with side effects.
With auth "none", remove `headers` from the servers file and do not set `MCP_SECRET_N8N`.

## Secrets
- Keep `.env` out of git. The repository `.gitignore` already ignores it.
- Make `.env` readable only by the owner, for example `chmod 600 .env`.
- Give the n8n token the least access it needs.
- If the token appears in a log or a chat, rotate it in n8n.

## How the pin works
The pin is a sha256 hash of the tool name, description and input schema.
When the description or the schema changes, the hash changes.
The hub then drops the tool until you update the pin.
This helps stop a changed server from giving the model new instructions without your check.
The pin does not cover the workflow behind the tool name. An edit of the workflow steps does not change the hash.
Tool results stay untrusted data.

## Troubleshooting
- Tool missing or silent: the pin is missing or wrong. Copy the value from the WARNING line `current hash sha256:...` into `tools`.
- Tool vanished after an edit in n8n: the description changed, so the hash changed. Read the new hash in the log and update the pin.
- n8n on a LAN or private address: the SSRF check blocks the host. The log shows `blocked address`. Add the host to `OTA_MCP_ALLOWED_HOSTS`.
- Server entry skipped: the log shows `mcp config entry 0 (name 'n8n') skipped:` and the reason. The hub skips only that entry and loads the other servers. Fix the entry, then restart the server. For example, `no_confirm` names a tool that is not a key of `tools`.
- n8n down at start: the hub waits at most 5 s, then starts without the n8n tools. The user hears no error. The tools are missing. Start n8n, then restart the server.
- Bearer secret missing: the log shows `missing variable MCP_SECRET_N8N`. Set `MCP_SECRET_N8N` in `.env`. Never write the secret in the servers file.
