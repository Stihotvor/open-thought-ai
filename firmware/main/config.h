// Device settings in NVS. The only module that touches NVS.
//
// NVS encryption is off in M1 (docs/security.md): the Wi-Fi password and the token are in
// plain flash. Never log them.
#pragma once

#include <stdbool.h>

typedef struct {
    char ssid[33];
    char wifi_pass[65];
    char url[129];
    char token[129];
    bool provisioned;
} config_t;

void config_init(void);

// Fill `out` from NVS. Missing keys are empty strings.
void config_load(config_t *out);

// Save the network settings. An empty token keeps the saved token. The `provisioned`
// flag is written last, after a commit, so a power cut never leaves a half-saved config
// marked as complete.
void config_save(const config_t *in);

// The SoftAP password: 12 random characters, made once and kept in NVS.
void config_ap_password(char out[13]);
