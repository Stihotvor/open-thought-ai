// SoftAP setup (docs/security.md, "Device and provisioning").
//
// A 5 s button hold starts a WPA2 access point with a per-device password and a web form
// at http://192.168.4.1/. The form needs a 6-digit PIN that is new on every start. Both are
// shown on the OLED. The access point stops after 5 minutes, after 5 wrong PINs, or after
// a save; the device then reboots.
#pragma once

void provision_start(void);
