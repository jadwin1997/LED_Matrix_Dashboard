// Classic ATmega328P Nano, WS2812B 22x22 panel, DIN on D3.
// Install FastLED using Arduino IDE's Library Manager.
#include <FastLED.h>

constexpr uint16_t LED_COUNT = 484;
constexpr uint8_t DATA_PIN = 3;
constexpr unsigned long BAUD = 250000;
static_assert(sizeof(CRGB) == 3, "Protocol requires packed three-byte RGB pixels");
CRGB leds[LED_COUNT];  // 1452 bytes; do not allocate another frame buffer.
const uint8_t MAGIC[] = {'M', 'X', '2', '2'};
uint8_t matched = 0;
unsigned long lastFrame = 0;
bool lit = false;

uint16_t crcByte(uint16_t crc, uint8_t value) {
  crc ^= (uint16_t)value << 8;
  for (uint8_t bit = 0; bit < 8; ++bit)
    crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
  return crc;
}

bool readByte(uint8_t &value) {
  unsigned long start = millis();
  while (!Serial.available()) {
    if (millis() - start > 500) return false;
  }
  value = Serial.read();
  return true;
}

void receiveFrame() {
  uint8_t *raw = reinterpret_cast<uint8_t *>(leds);
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < LED_COUNT * 3; ++i) {
    uint8_t value;
    if (!readByte(value)) {
      Serial.println(F("ERR timeout"));
      return;
    }
    raw[i] = value;
    crc = crcByte(crc, value);
  }
  uint8_t low, high;
  if (!readByte(low) || !readByte(high)) {
    Serial.println(F("ERR timeout"));
    return;
  }
  if (crc != ((uint16_t)high << 8 | low)) {
    Serial.println(F("ERR crc"));
    return;
  }
  // Host waits for OK: no serial bytes may arrive during show().
  FastLED.show();
  lastFrame = millis();
  lit = true;
  Serial.println(F("OK"));
}

void setup() {
  FastLED.addLeds<WS2812B, DATA_PIN, GRB>(leds, LED_COUNT);
  FastLED.setBrightness(255);  // Brightness is applied by the host.
  FastLED.clear(true);
  Serial.begin(BAUD);
  Serial.println(F("READY MX22 1"));
}

void loop() {
  if (Serial.available()) {
    uint8_t value = Serial.read();
    if (value == '?' && matched == 0) {
      Serial.println(F("READY MX22 1"));
    } else if (value == MAGIC[matched]) {
      if (++matched == sizeof(MAGIC)) {
        matched = 0;
        receiveFrame();
      }
    } else {
      matched = (value == MAGIC[0]) ? 1 : 0;
    }
  }
  // Fail dark if the app dies, disconnects, or the PC goes to sleep.
  if (lit && millis() - lastFrame > 3000) {
    FastLED.clear(true);
    lit = false;
  }
}
