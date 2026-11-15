#include "proto.h"

#include <string.h>

bool proto_shallow(const char *data, int len)
{
    int depth = 0;
    for (int i = 0; i < len; i++) {
        if (data[i] == '{' || data[i] == '[') {
            if (++depth > PROTO_MAX_NESTING) {
                return false;
            }
        } else if (data[i] == '}' || data[i] == ']') {
            depth--;
        }
    }
    return true;
}

static int whole_in_range(double value, int max)
{
    // NaN fails both comparisons. The cast is safe only after the range check.
    if (!(value >= 0 && value <= max) || value != (double)(int)value) {
        return -1;
    }
    return (int)value;
}

int proto_turn(double value)
{
    return whole_in_range(value, 255);
}

int proto_volume(double value)
{
    return whole_in_range(value, 100);
}

bool proto_safe_id(const char *id)
{
    if (strlen(id) > PROTO_MAX_ID) {
        return false;
    }
    for (const unsigned char *c = (const unsigned char *)id; *c; c++) {
        if (*c < 0x20 || *c == '"' || *c == '\\') {
            return false;
        }
    }
    return true;
}
