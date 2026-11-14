#include "validate.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

const char *validate_ssid(const char *ssid)
{
    size_t n = strlen(ssid);
    if (n < 1 || n > 32) {
        return "Wi-Fi name must have 1 to 32 characters.";
    }
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)ssid[i];
        if (c < 0x20 || c == 0x7F) {
            return "Wi-Fi name has a control character.";
        }
    }
    return NULL;
}

const char *validate_wifi_pass(const char *pass)
{
    size_t n = strlen(pass);
    if (n == 0) {
        return NULL;  // an open network
    }
    if (n == 64) {
        for (size_t i = 0; i < n; i++) {
            if (!isxdigit((unsigned char)pass[i])) {
                return "A 64-character Wi-Fi key must be hex.";
            }
        }
        return NULL;
    }
    if (n < 8 || n > 63) {
        return "Wi-Fi password must have 8 to 63 characters.";
    }
    for (size_t i = 0; i < n; i++) {
        if (pass[i] < 0x20 || pass[i] > 0x7E) {
            return "Wi-Fi password must be printable ASCII.";
        }
    }
    return NULL;
}

const char *validate_url(const char *url)
{
    static const char *const BAD = "Server URL must look like ws://host:port/path.";
    if (strlen(url) > 128) {
        return "Server URL is too long.";
    }
    if (strncmp(url, "ws://", 5) != 0) {
        return BAD;  // wss:// comes after the M2 TLS spike
    }
    const char *host = url + 5;
    const char *colon = strchr(host, ':');
    if (colon == NULL || colon == host) {
        return BAD;
    }
    for (const char *c = host; c < colon; c++) {
        if (!isalnum((unsigned char)*c) && *c != '.' && *c != '-') {
            return BAD;
        }
    }
    const char *port = colon + 1;
    char *end;
    long value = strtol(port, &end, 10);
    if (end == port || value < 1 || value > 65535 || (*end != '\0' && *end != '/')) {
        return BAD;
    }
    for (const char *c = end; *c; c++) {
        if (*c <= 0x20 || *c > 0x7E) {
            return BAD;
        }
    }
    return NULL;
}

const char *validate_token(const char *token)
{
    size_t n = strlen(token);
    if (n == 0) {
        return NULL;  // keep the saved token
    }
    if (n < 43 || n > 128) {
        return "Token must have 43 to 128 characters.";
    }
    for (size_t i = 0; i < n; i++) {
        char c = token[i];
        if (!isalnum((unsigned char)c) && c != '-' && c != '_') {
            return "Token must use only A-Z, a-z, 0-9, - and _.";
        }
    }
    return NULL;
}
