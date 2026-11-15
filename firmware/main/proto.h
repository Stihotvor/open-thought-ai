// Pure checks for protocol v1 input from the server (docs/protocol.md). No ESP-IDF:
// host tests run them on the PC (firmware/test).
#pragma once

#include <stdbool.h>

#define PROTO_MAX_NESTING 4  // our messages are flat: deeper JSON is refused before parsing
#define PROTO_MAX_ID 48      // the longest tool call id we echo back

// True when the JSON text nests no deeper than PROTO_MAX_NESTING. cJSON parses by
// recursion, so this runs before the parser. Brackets inside strings do not count.
bool proto_shallow(const char *data, int len);

// A JSON number as a turn id 0..255, or -1 when it is not a whole number in range.
int proto_turn(double value);

// A JSON number as a volume 0..100, or -1 when it is not a whole number in range.
int proto_volume(double value);

// True when a tool call id can go into a JSON string with no escape: at most
// PROTO_MAX_ID characters, no control characters, no quote and no backslash.
bool proto_safe_id(const char *id);
