// ESP32 실제 장치 -> QEAAI 디지털 트윈 (MQTT)
//
// 필요: Arduino IDE + ESP32 보드 패키지, 라이브러리 "PubSubClient" (Nick O'Leary), "ArduinoJson" 7.x
// QEAAI 쪽: ./run.sh --mqtt-bind 0.0.0.0   (같은 Wi-Fi 의 장치가 접속할 수 있게)
//           인증을 쓰면 QEAAI_MQTT_USER / QEAAI_MQTT_PASSWORD 환경변수 + 아래 MQTT_USER / MQTT_PASS
//
// 동작: 1 초마다 실제 센서값(아날로그 핀, 칩 내부 온도, Wi-Fi 신호)을 qeaai/things/<id>/state 로 보내고,
//       qeaai/neural/intent 를 구독해 근육 의도 "fist" 면 LED 켜기, "open" 이면 끄기.

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

const char* WIFI_SSID  = "YOUR_WIFI";
const char* WIFI_PASS  = "YOUR_PASSWORD";
const char* MQTT_HOST  = "192.168.0.10";   // QEAAI 를 실행한 컴퓨터의 IP
const int   MQTT_PORT  = 1883;
const char* MQTT_USER  = nullptr;          // 인증 사용 시 "user"
const char* MQTT_PASS  = nullptr;
const char* THING_ID   = "esp32-01";
const int   SENSOR_PIN = 34;               // 아날로그 센서 (조도, 압력, EMG 모듈 등)
const int   LED_PIN    = 2;

WiFiClient net;
PubSubClient mqtt(net);
String stateTopic = String("qeaai/things/") + THING_ID + "/state";

void onMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) return;
  if (!doc["trusted"].as<bool>()) return;          // Allied Identity 가 인증한 사용자만
  const char* intent = doc["intent"] | "rest";
  if (!strcmp(intent, "fist")) digitalWrite(LED_PIN, HIGH);
  if (!strcmp(intent, "open")) digitalWrite(LED_PIN, LOW);
}

void connectMqtt() {
  while (!mqtt.connected()) {
    if (mqtt.connect(THING_ID, MQTT_USER, MQTT_PASS)) {
      mqtt.subscribe("qeaai/neural/intent");
    } else {
      delay(2000);
    }
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(1024);
}

unsigned long lastSent = 0;

void loop() {
  connectMqtt();
  mqtt.loop();
  if (millis() - lastSent < 1000) return;
  lastSent = millis();

  JsonDocument doc;
  doc["kind"]        = "esp32_sensor";
  doc["analog_raw"]  = analogRead(SENSOR_PIN);
  doc["chip_temp_c"] = temperatureRead();
  doc["rssi_dbm"]    = WiFi.RSSI();
  doc["led_on"]      = digitalRead(LED_PIN) == HIGH;
  doc["uptime_s"]    = millis() / 1000;
  // doc["x"] = -90; doc["y"] = 30;   // VR 안 위치를 직접 지정하려면 (m)
  char buf[512];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(stateTopic.c_str(), (const uint8_t*)buf, n, false);
}
