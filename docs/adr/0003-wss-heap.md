# ADR 0003: wss:// on the ESP32-C3 SuperMini

Status: accepted (2026-11-16). The spike code is on the local branch `spike/wss`.

## Context
The device link uses `ws://`. On a shared LAN, anyone on the network can read the device token and the audio
(see [security.md](../security.md)). The plan asks for a `wss://` spike with a heap measurement.

The free heap target is at least 40 KB (M6). The link needs 1.5 s audio buffers in each direction (48 KB
reply, 48 KB mic), because the 2 dBm radio stalls the link for up to 1.2 s.

## Spike
- `esp_websocket_client` with `wss://`, a self-signed EC P-256 test cert pinned in the firmware (`cert_pem`),
  and uvicorn with TLS on port 8443.
- The link connects, and echo turns work over TLS. Audio sends still stall for up to 1.24 s, and the buffers
  cover the stalls.

| Setup | Free heap, idle | Minimum since boot | Largest block |
|---|---|---|---|
| `ws://` | 50.5 KB | 43.5 KB | 40 KB |
| `wss://`, mbedTLS defaults | 22.8 KB | 16.8 KB | 11.8 KB |
| `wss://`, mbedTLS savers, idle | 45.3 KB | 35.9 KB | 32 KB |
| `wss://`, mbedTLS savers, after 2 echo turns | 42.4 KB | 29.2 KB | 27 KB |

The savers are `MBEDTLS_DYNAMIC_BUFFER`, `MBEDTLS_DYNAMIC_FREE_CONFIG_DATA`, `MBEDTLS_DYNAMIC_FREE_CA_CERT`,
and `MBEDTLS_SSL_IN_CONTENT_LEN=6144`. The smaller input buffer works because the server sends records of at
most 3.3 KB. A server that sends larger TLS records breaks the link.

## Options
1. Keep `ws://` on the LAN until M7 (public `wss://`), as the milestone table plans. Accept the sniffing risk
   on a trusted home network.
2. Use `wss://` now and shrink the audio buffers to about 1.1 s each, to win back 11 KB. Stalls longer than
   1.1 s then break the audio again.
3. Use `wss://` now and win the RAM back elsewhere (fewer task stacks, smaller WebSocket buffer, Wi-Fi
   buffer counts). This needs measurement and work in M6.

## Decision
Option 1. The device keeps `ws://` on the LAN until M7 (public `wss://`).

## Consequences
- On a shared LAN, anyone on the network can read the device token and the audio. Use a trusted home network.
- M7 starts from this spike: the mbedTLS savers, a pinned cert, and about 11 KB of RAM to win back.
