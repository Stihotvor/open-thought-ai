#include "face.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "board_pins.h"
#include "driver/i2c_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_ssd1306.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#define WIDTH 128
#define HEIGHT 64

// SSD1306 memory layout: 8 pages of 8 pixel rows; one byte is one column of a page,
// bit 0 at the top.
static uint8_t s_frame[WIDTH * HEIGHT / 8];
static esp_lcd_panel_handle_t s_panel;
static SemaphoreHandle_t s_lock;  // faces can be set from the button and app tasks

static void pixel(int x, int y)
{
    if (x >= 0 && x < WIDTH && y >= 0 && y < HEIGHT) {
        s_frame[x + (y / 8) * WIDTH] |= 1 << (y % 8);
    }
}

static void fill_rect(int x, int y, int w, int h)
{
    for (int i = x; i < x + w; i++) {
        for (int j = y; j < y + h; j++) {
            pixel(i, j);
        }
    }
}

// A filled rectangle with corners cut by radius r.
static void fill_round_rect(int x, int y, int w, int h, int r)
{
    for (int j = 0; j < h; j++) {
        int dy = (j < r) ? r - j : (j >= h - r ? j - (h - 1 - r) : 0);
        int inset = 0;
        while (inset < r && (r - inset) * (r - inset) + dy * dy > r * r) {
            inset++;
        }
        if (dy == 0) {
            inset = 0;
        }
        fill_rect(x + inset, y + j, w - 2 * inset, 1);
    }
}

static void line(int x0, int y0, int x1, int y1)
{
    int dx = x1 > x0 ? x1 - x0 : x0 - x1;
    int dy = y1 > y0 ? y0 - y1 : y1 - y0;  // negative
    int sx = x0 < x1 ? 1 : -1;
    int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    for (;;) {
        fill_rect(x0, y0, 2, 2);  // 2 px wide strokes read better on a small OLED
        if (x0 == x1 && y0 == y1) {
            return;
        }
        int e2 = 2 * err;
        if (e2 >= dy) {
            err += dy;
            x0 += sx;
        }
        if (e2 <= dx) {
            err += dx;
            y0 += sy;
        }
    }
}

// 5x7 font, one byte per column, bit 0 at the top.
static const uint8_t DIGITS[10][5] = {
    {0x3E, 0x51, 0x49, 0x45, 0x3E}, {0x00, 0x42, 0x7F, 0x40, 0x00},
    {0x42, 0x61, 0x51, 0x49, 0x46}, {0x21, 0x41, 0x45, 0x4B, 0x31},
    {0x18, 0x14, 0x12, 0x7F, 0x10}, {0x27, 0x45, 0x45, 0x45, 0x39},
    {0x3C, 0x4A, 0x49, 0x49, 0x30}, {0x01, 0x71, 0x09, 0x05, 0x03},
    {0x36, 0x49, 0x49, 0x49, 0x36}, {0x06, 0x49, 0x49, 0x29, 0x1E},
};
static const uint8_t LETTERS[26][5] = {
    {0x7E, 0x11, 0x11, 0x11, 0x7E}, {0x7F, 0x49, 0x49, 0x49, 0x36},
    {0x3E, 0x41, 0x41, 0x41, 0x22}, {0x7F, 0x41, 0x41, 0x22, 0x1C},
    {0x7F, 0x49, 0x49, 0x49, 0x41}, {0x7F, 0x09, 0x09, 0x09, 0x01},
    {0x3E, 0x41, 0x49, 0x49, 0x7A}, {0x7F, 0x08, 0x08, 0x08, 0x7F},
    {0x00, 0x41, 0x7F, 0x41, 0x00}, {0x20, 0x40, 0x41, 0x3F, 0x01},
    {0x7F, 0x08, 0x14, 0x22, 0x41}, {0x7F, 0x40, 0x40, 0x40, 0x40},
    {0x7F, 0x02, 0x0C, 0x02, 0x7F}, {0x7F, 0x04, 0x08, 0x10, 0x7F},
    {0x3E, 0x41, 0x41, 0x41, 0x3E}, {0x7F, 0x09, 0x09, 0x09, 0x06},
    {0x3E, 0x41, 0x51, 0x21, 0x5E}, {0x7F, 0x09, 0x19, 0x29, 0x46},
    {0x46, 0x49, 0x49, 0x49, 0x31}, {0x01, 0x01, 0x7F, 0x01, 0x01},
    {0x3F, 0x40, 0x40, 0x40, 0x3F}, {0x1F, 0x20, 0x40, 0x20, 0x1F},
    {0x3F, 0x40, 0x38, 0x40, 0x3F}, {0x63, 0x14, 0x08, 0x14, 0x63},
    {0x07, 0x08, 0x70, 0x08, 0x07}, {0x61, 0x51, 0x49, 0x45, 0x43},
};
static const uint8_t COLON[5] = {0x00, 0x36, 0x36, 0x00, 0x00};
static const uint8_t DASH[5] = {0x08, 0x08, 0x08, 0x08, 0x08};
static const uint8_t DOT[5] = {0x00, 0x60, 0x60, 0x00, 0x00};
static const uint8_t UNDERSCORE[5] = {0x40, 0x40, 0x40, 0x40, 0x40};

static const uint8_t *glyph(char c)
{
    if (c >= '0' && c <= '9') return DIGITS[c - '0'];
    if (c >= 'A' && c <= 'Z') return LETTERS[c - 'A'];
    if (c >= 'a' && c <= 'z') return LETTERS[c - 'a'];
    switch (c) {
        case ':': return COLON;
        case '-': return DASH;
        case '.': return DOT;
        case '_': return UNDERSCORE;
        default: return NULL;  // a space
    }
}

static void text(int x, int y, const char *s)
{
    for (int n = 0; s[n] && n < FACE_TEXT_COLUMNS; n++, x += 6) {
        const uint8_t *g = glyph(s[n]);
        for (int col = 0; g && col < 5; col++) {
            for (int row = 0; row < 7; row++) {
                if (g[col] & (1 << row)) {
                    pixel(x + col, y + row);
                }
            }
        }
    }
}

static void eyes(int dx, int dy, int h)
{
    int y = 22 + dy - h / 2;
    fill_round_rect(24 + dx, y, 28, h, 8);
    fill_round_rect(76 + dx, y, 28, h, 8);
}

static void x_eye(int cx)
{
    line(cx - 10, 12, cx + 10, 32);
    line(cx - 10, 32, cx + 10, 12);
}

static void draw(face_t face)
{
    switch (face) {
        case FACE_IDLE:
            eyes(0, 4, 26);
            break;
        case FACE_LISTENING:  // wide open
            eyes(0, 4, 34);
            break;
        case FACE_THINKING:  // looking up, three dots
            eyes(6, -6, 22);
            fill_rect(54, 52, 4, 4);
            fill_rect(62, 52, 4, 4);
            fill_rect(70, 52, 4, 4);
            break;
        case FACE_SPEAKING:  // open mouth
            eyes(0, 0, 24);
            fill_round_rect(52, 44, 24, 14, 6);
            break;
        case FACE_OFFLINE:
            x_eye(38);
            x_eye(90);
            text(40, 50, "NO WIFI");
            break;
        case FACE_ERROR:
            x_eye(38);
            x_eye(90);
            text(46, 50, "ERROR");
            break;
    }
}

static void flush(void)
{
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(s_panel, 0, 0, WIDTH, HEIGHT, s_frame));
}

void face_show(face_t face)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memset(s_frame, 0, sizeof(s_frame));
    draw(face);
    flush();
    xSemaphoreGive(s_lock);
}

void face_show_text(const char *const lines[], int count)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memset(s_frame, 0, sizeof(s_frame));
    for (int i = 0; i < count && i < FACE_TEXT_LINES; i++) {
        text(1, 2 + i * 10, lines[i]);
    }
    flush();
    xSemaphoreGive(s_lock);
}

void face_init(void)
{
    s_lock = xSemaphoreCreateMutex();
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = PIN_OLED_SDA,
        .scl_io_num = PIN_OLED_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus;
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_config, &bus));

    const esp_lcd_panel_io_i2c_config_t io_config = {
        .dev_addr = OLED_I2C_ADDR,
        .scl_speed_hz = 400 * 1000,
        .control_phase_bytes = 1,
        .dc_bit_offset = 6,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    esp_lcd_panel_io_handle_t io;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_i2c(bus, &io_config, &io));

    esp_lcd_panel_ssd1306_config_t ssd1306_config = {.height = HEIGHT};
    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = GPIO_NUM_NC,
        .bits_per_pixel = 1,
        .vendor_config = &ssd1306_config,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_ssd1306(io, &panel_config, &s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel));
    // This module lights a pixel for a 0 bit without inversion.
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(s_panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(s_panel, true));
}
