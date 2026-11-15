// Host tests for the protocol input checks. Run: make -C firmware/test
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "proto.h"

static int failures;

static void check(const char *what, int ok)
{
    if (!ok) {
        printf("FAIL: %s\n", what);
        failures++;
    }
}

#define CHECK(expr) check(#expr, (expr))

static int shallow(const char *s)
{
    return proto_shallow(s, (int)strlen(s));
}

int main(void)
{
    CHECK(shallow("{\"type\":\"hello\",\"proto\":1}"));
    CHECK(shallow("{\"a\":[{\"b\":[1]}]}"));         // depth 4
    CHECK(!shallow("{\"a\":[{\"b\":[{}]}]}"));       // depth 5
    CHECK(!shallow("[[[[[[[[[[[[[[[[[[[[[[[["));
    CHECK(shallow("{}{}{}{}{}{}"));                  // siblings do not add depth
    CHECK(shallow(""));
    CHECK(proto_shallow("[[[[[", 4));                // only `len` bytes count
    CHECK(shallow("{\"a\":\"[[[[[[[[\"}"));             // brackets in a string do not count
    CHECK(!shallow("[\"]]]]]]]]\",[[[[[[[[[[["));         // closers in a string do not hide depth
    CHECK(!shallow("[\"\\\"]]]]]]\",[[[[[[[[["));      // nor after an escaped quote

    CHECK(proto_turn(0) == 0);
    CHECK(proto_turn(255) == 255);
    CHECK(proto_turn(256) == -1);
    CHECK(proto_turn(-1) == -1);
    CHECK(proto_turn(1.5) == -1);
    CHECK(proto_turn(NAN) == -1);
    CHECK(proto_turn(INFINITY) == -1);
    CHECK(proto_turn(1e300) == -1);

    CHECK(proto_volume(0) == 0);
    CHECK(proto_volume(100) == 100);
    CHECK(proto_volume(101) == -1);
    CHECK(proto_volume(-0.5) == -1);

    CHECK(proto_safe_id("call_1-abc"));
    CHECK(proto_safe_id(""));
    CHECK(proto_safe_id("zażółć"));                  // UTF-8 needs no escape
    CHECK(!proto_safe_id("a\"b"));
    CHECK(!proto_safe_id("a\\b"));
    CHECK(!proto_safe_id("a\nb"));
    char id[64];
    memset(id, 'x', PROTO_MAX_ID), id[PROTO_MAX_ID] = '\0';
    CHECK(proto_safe_id(id));
    id[PROTO_MAX_ID] = 'x', id[PROTO_MAX_ID + 1] = '\0';
    CHECK(!proto_safe_id(id));

    printf("%s (%d failures)\n", failures ? "FAILED" : "OK", failures);
    return failures ? 1 : 0;
}
