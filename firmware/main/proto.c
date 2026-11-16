#include "proto.h"

#include <string.h>

bool proto_shallow(const char *data, int len)
{
    int depth = 0;
    bool in_string = false;
    for (int i = 0; i < len; i++) {
        char c = data[i];
        if (in_string) {
            if (c == '\\') {
                i++;  // skip the escaped character
            } else if (c == '"') {
                in_string = false;
            }
        } else if (c == '"') {
            in_string = true;
        } else if (c == '{' || c == '[') {
            if (++depth > PROTO_MAX_NESTING) {
                return false;
            }
        } else if ((c == '}' || c == ']') && depth > 0) {
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
