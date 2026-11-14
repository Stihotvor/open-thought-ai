// Wi-Fi owner: station mode (with reconnect backoff) and the setup access point.
#pragma once

#include <stdbool.h>

// Called from the event task when the station gets or loses an IP address.
typedef void (*net_handler_t)(bool online);

// Starts the radio idle in station mode (needed for a true random number source).
void net_init(net_handler_t handler);

// Connect as a station and keep reconnecting with a 1..60 s backoff.
void net_connect(const char *ssid, const char *password);

// Stop the station and start a WPA2 access point for one client. Leaving setup reboots.
void net_start_ap(const char *ssid, const char *password);

// Fill `out` with "OTA-XXXX" from the last two MAC bytes.
void net_ap_ssid(char out[9]);
