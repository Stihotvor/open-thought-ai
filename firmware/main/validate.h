// Checks for the setup form fields. Pure C with no ESP-IDF calls, so the host tests in
// firmware/test can run them. Each returns NULL when the value is valid, or a short
// error message for the form.
#pragma once

const char *validate_ssid(const char *ssid);
const char *validate_wifi_pass(const char *pass);
const char *validate_url(const char *url);  // ws://host:port[/path] only
const char *validate_token(const char *token);  // empty means: keep the saved token
