// Host tests for the setup form checks. Run: make -C firmware/test
#include <stdio.h>
#include <string.h>

#include "validate.h"

static int failures;

#define VALID(call) check(#call, (call) == NULL)
#define INVALID(call) check(#call, (call) != NULL)

static void check(const char *what, int ok)
{
    if (!ok) {
        printf("FAIL: %s\n", what);
        failures++;
    }
}

int main(void)
{
    char s[200];

    VALID(validate_ssid("Home"));
    memset(s, 'a', 32), s[32] = '\0';
    VALID(validate_ssid(s));
    s[32] = 'a', s[33] = '\0';
    INVALID(validate_ssid(s));
    INVALID(validate_ssid(""));
    INVALID(validate_ssid("bad\x01name"));

    VALID(validate_wifi_pass(""));  // open network
    VALID(validate_wifi_pass("12345678"));
    INVALID(validate_wifi_pass("1234567"));
    memset(s, 'f', 64), s[64] = '\0';
    VALID(validate_wifi_pass(s));  // 64 hex characters
    s[0] = 'g';
    INVALID(validate_wifi_pass(s));
    INVALID(validate_wifi_pass("password\x7f"));

    VALID(validate_url("ws://192.168.1.10:8000/ws"));
    VALID(validate_url("ws://home-server.lan:8000"));
    INVALID(validate_url("wss://host:443/ws"));  // after the TLS spike
    INVALID(validate_url("http://host:80/"));
    INVALID(validate_url("ws://host/ws"));  // no port
    INVALID(validate_url("ws://:8000/ws"));  // no host
    INVALID(validate_url("ws://host:0/ws"));
    INVALID(validate_url("ws://host:65536/ws"));
    INVALID(validate_url("ws://host:80x/ws"));
    INVALID(validate_url("ws://user@host:80/ws"));
    INVALID(validate_url("ws://host:80/a b"));
    memset(s, 'a', 140), s[140] = '\0';
    memcpy(s, "ws://h:1/", 9);
    INVALID(validate_url(s));  // too long

    VALID(validate_token(""));  // keep the saved one
    memset(s, 'A', 43), s[43] = '\0';
    VALID(validate_token(s));
    s[42] = '\0';
    INVALID(validate_token(s));
    s[42] = '+', s[43] = '\0';
    INVALID(validate_token(s));  // not base64url
    memset(s, '_', 129), s[129] = '\0';
    INVALID(validate_token(s));

    printf("%s (%d failures)\n", failures ? "FAILED" : "OK", failures);
    return failures != 0;
}
