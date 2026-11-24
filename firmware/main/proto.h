// Pure checks for protocol v1 input from the server (docs/protocol.md). No ESP-IDF:
// host tests run them on the PC (firmware/test).
#pragma once

#include <stdbool.h>

#define PROTO_MAX_NESTING 4  // our messages are flat: deeper JSON is refused before parsing
#define PROTO_MAX_TOOL_ID 1000000000  // the server counts calls per session from 1

// True when the JSON text nests no deeper than PROTO_MAX_NESTING. cJSON parses by
// recursion, so this runs before the parser. Brackets inside strings do not count.
bool proto_shallow(const char *data, int len);

// A JSON number as a turn id 0..255, or -1 when it is not a whole number in range.
int proto_turn(double value);

// A JSON number as a volume 0..100, or -1 when it is not a whole number in range.
int proto_volume(double value);

// A JSON number as a tool call id 1..PROTO_MAX_TOOL_ID, or -1 when it is not valid.
int proto_tool_id(double value);

// A set_face name as its face_t value (face.h), or -1 for an unknown name.
int proto_face(const char *name);
