// OLED faces for the device states, and a text screen for setup.
#pragma once

typedef enum {
    FACE_IDLE,
    FACE_LISTENING,
    FACE_THINKING,
    FACE_SPEAKING,
    FACE_OFFLINE,
    FACE_ERROR,
    FACE_CONFIRM,  // the server waits for a spoken yes
} face_t;

void face_init(void);
void face_show(face_t face);

// Up to FACE_TEXT_LINES lines of up to FACE_TEXT_COLUMNS characters. The font has
// digits, A-Z, space and ".:-_"; lower case is shown as upper case, other characters as
// a space.
#define FACE_TEXT_LINES 6
#define FACE_TEXT_COLUMNS 21
void face_show_text(const char *const lines[], int count);

// A QR code of `qr_text` on the left half, and up to FACE_QR_LINES lines of up to
// FACE_QR_COLUMNS characters on the right. Without room for the code, only the lines.
#define FACE_QR_LINES 7
#define FACE_QR_COLUMNS 11
void face_show_qr(const char *qr_text, const char *const lines[], int count);
