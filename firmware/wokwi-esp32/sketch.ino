/*
 * AgroSmart · Fase 6 · Estação de sensoriamento de campo (ESP32 no Wokwi)
 * ---------------------------------------------------------------------------
 * Uma estação do talhão TLH-03 (sensor SNS-0301). Lê cinco sensores, sincroniza
 * o relógio por NTP e publica a cada INTERVALO_MS, por MQTT, um JSON com as
 * chaves do contrato de dados `contratos/sensores.v1.yaml`:
 *
 *   agrosmart/sensores/SNS-0301/leitura
 *   {"sensor_id":"SNS-0301","talhao_id":"TLH-03","medido_em":"2026-10-08T14:32:05Z",
 *    "umidade_solo_pct":41.2,"temperatura_solo_c":24.8,"temperatura_ar_c":27.1,
 *    "umidade_ar_pct":58.0,"bateria_pct":83.6,"rssi_dbm":-61,"luminosidade_lux":8123.0}
 *
 * `luminosidade_lux` NÃO está no contrato v1: o gateway (`agrosmart coletar-mqtt`)
 * descarta a chave e registra a ocorrência. Entra no contrato v2 (roadmap).
 *
 * Atuador: o LED verde "irrigação" obedece ao comando publicado em
 *   agrosmart/atuadores/TLH-03/irrigacao   {"acao":"ligar","minutos":10}  |  {"acao":"desligar"}
 * e o estado é devolvido em agrosmart/atuadores/TLH-03/irrigacao/estado.
 * O LED vermelho acende quando a bateria fica abaixo de 15 % (regra SEN-10).
 *
 * Sensores (todos em pinos ADC1 — o ADC2 do ESP32 não funciona com Wi-Fi ligado):
 *   DHT22          GPIO 4    temperatura_ar_c, umidade_ar_pct
 *   potenciômetro  GPIO 34   umidade_solo_pct (sonda capacitiva emulada, 0–100 %)
 *   NTC 10k        GPIO 35   temperatura_solo_c
 *   LDR            GPIO 32   luminosidade_lux (só no payload e no monitor serial)
 *   potenciômetro  GPIO 33   bateria_pct (divisor de tensão da bateria emulado)
 *   LED verde      GPIO 26   irrigação ligada
 *   LED vermelho   GPIO 27   alerta de bateria
 *
 * Bibliotecas (libraries.txt): PubSubClient, ArduinoJson, DHT sensor library (Adafruit) + Adafruit Unified Sensor.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <time.h>
#include <math.h>

// ---------------------------------------------------------------------------
// Configuração — tudo que se ajusta está aqui no topo
// ---------------------------------------------------------------------------
const char* SENSOR_ID = "SNS-0301";   // padrão do contrato: ^SNS-\d{4}$
const char* TALHAO_ID = "TLH-03";     // precisa existir no ERP (regra SEN-09)

const char* WIFI_SSID  = "Wokwi-GUEST";   // rede simulada do Wokwi, sem senha
const char* WIFI_SENHA = "";

// Broker público (sem credenciais). Alternativas:
//   "test.mosquitto.org"    — outro broker público
//   "host.wokwi.internal"   — mosquitto local (infra/docker-compose.yml) via
//                             Wokwi Private Gateway (wokwigw); ver README.md
const char* MQTT_HOST  = "broker.hivemq.com";
const uint16_t MQTT_PORTA = 1883;

const unsigned long INTERVALO_MS = 5000;   // 5 s na demonstração; 900000 (15 min) em campo
const char* NTP_SERVIDOR = "pool.ntp.org";

// Pinos
const int PINO_DHT          = 4;
const int PINO_UMIDADE_SOLO = 34;
const int PINO_NTC_SOLO     = 35;
const int PINO_LDR          = 32;
const int PINO_BATERIA      = 33;
const int PINO_LED_IRRIGACAO = 26;
const int PINO_LED_BATERIA   = 27;

// Calibração
const float BATERIA_ALERTA_PCT = 15.0;  // SEN-10: abaixo disso é ordem de manutenção
const float ADC_MAX = 4095.0;           // ESP32: 12 bits
const float NTC_BETA = 3950.0;          // NTC 10k do Wokwi
const float LDR_GAMMA = 0.7;            // curva do fotoresistor do Wokwi
const float LDR_RL10 = 50.0;            // kΩ a 10 lux

// ---------------------------------------------------------------------------
// Estado
// ---------------------------------------------------------------------------
WiFiClient rede;
PubSubClient mqtt(rede);
DHT dht(PINO_DHT, DHT22);

String topicoLeitura;
String topicoAtuador;
String topicoEstado;

bool irrigacaoLigada = false;
unsigned long irrigacaoAteMs = 0;
unsigned long ultimaPublicacaoMs = 0;
uint32_t sequencia = 0;

// ---------------------------------------------------------------------------
// Rede e relógio
// ---------------------------------------------------------------------------
void conectarWiFi() {
  Serial.printf("[wifi] conectando a %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_SENHA, 6);  // canal 6: conexão instantânea no Wokwi
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.printf(" ok · IP %s · RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
}

void sincronizarRelogio() {
  // Fuso 0 e sem horário de verão: o contrato exige `medido_em` em UTC.
  configTime(0, 0, NTP_SERVIDOR, "time.google.com");
  Serial.print("[ntp] sincronizando");
  time_t agora = time(nullptr);
  int tentativas = 0;
  while (agora < 1700000000 && tentativas < 80) {  // antes de nov/2023 = relógio não acertado
    delay(250);
    Serial.print(".");
    agora = time(nullptr);
    tentativas++;
  }
  if (agora < 1700000000) {
    Serial.println(" FALHOU — leituras ficariam com relógio errado (SEN-08); reiniciando");
    delay(2000);
    ESP.restart();
  }
  char iso[25];
  instanteIso(iso, sizeof(iso));
  Serial.printf(" ok · %s\n", iso);
}

void instanteIso(char* saida, size_t tamanho) {
  time_t agora = time(nullptr);
  struct tm utc;
  gmtime_r(&agora, &utc);
  strftime(saida, tamanho, "%Y-%m-%dT%H:%M:%SZ", &utc);
}

// ---------------------------------------------------------------------------
// Sensores
// ---------------------------------------------------------------------------
float arredondar1(float valor) { return roundf(valor * 10.0f) / 10.0f; }

float lerUmidadeSolo() {
  // Sonda capacitiva real: tensão cai com a umidade e precisa de calibração
  // seco/água. Aqui o potenciômetro entrega o percentual direto: 0 % = seco.
  return arredondar1(analogRead(PINO_UMIDADE_SOLO) * 100.0f / ADC_MAX);
}

float lerTemperaturaSolo() {
  // NTC 10k em divisor com 10k (modelo do Wokwi), equação beta.
  int bruto = analogRead(PINO_NTC_SOLO);
  if (bruto <= 0 || bruto >= ADC_MAX) return NAN;
  float celsius = 1.0f / (logf(1.0f / (ADC_MAX / bruto - 1.0f)) / NTC_BETA + 1.0f / 298.15f) - 273.15f;
  return arredondar1(celsius);
}

float lerLuminosidade() {
  // Fórmula do fotoresistor do Wokwi (divisor com 2 kΩ), adaptada para 3,3 V / 12 bits.
  int bruto = analogRead(PINO_LDR);
  float tensao = bruto / ADC_MAX * 3.3f;
  if (tensao >= 3.29f) return 100000.0f;
  float resistencia = 2000.0f * tensao / (1.0f - tensao / 3.3f);
  if (resistencia <= 0.0f) return 100000.0f;
  float lux = powf(LDR_RL10 * 1e3f * powf(10.0f, LDR_GAMMA) / resistencia, 1.0f / LDR_GAMMA);
  return roundf(lux);
}

float lerBateria() {
  // Em campo: divisor de tensão da bateria Li-ion (3,0 V = 0 %, 4,2 V = 100 %).
  return arredondar1(analogRead(PINO_BATERIA) * 100.0f / ADC_MAX);
}

// ---------------------------------------------------------------------------
// Atuador de irrigação
// ---------------------------------------------------------------------------
void publicarEstadoIrrigacao() {
  JsonDocument doc;
  char iso[25];
  instanteIso(iso, sizeof(iso));
  doc["talhao_id"] = TALHAO_ID;
  doc["irrigacao"] = irrigacaoLigada ? "ligada" : "desligada";
  doc["restante_min"] = irrigacaoLigada ? (int)((irrigacaoAteMs - millis()) / 60000UL) : 0;
  doc["atualizado_em"] = iso;
  char buffer[192];
  size_t n = serializeJson(doc, buffer, sizeof(buffer));
  mqtt.publish(topicoEstado.c_str(), buffer, n);
}

void ligarIrrigacao(int minutos) {
  irrigacaoLigada = true;
  irrigacaoAteMs = millis() + (unsigned long)minutos * 60000UL;
  digitalWrite(PINO_LED_IRRIGACAO, HIGH);
  Serial.printf("[atuador] irrigação LIGADA por %d min\n", minutos);
  publicarEstadoIrrigacao();
}

void desligarIrrigacao(const char* motivo) {
  irrigacaoLigada = false;
  digitalWrite(PINO_LED_IRRIGACAO, LOW);
  Serial.printf("[atuador] irrigação DESLIGADA (%s)\n", motivo);
  publicarEstadoIrrigacao();
}

void aoReceberComando(char* topico, byte* payload, unsigned int tamanho) {
  JsonDocument cmd;
  DeserializationError erro = deserializeJson(cmd, payload, tamanho);
  if (erro) {
    Serial.printf("[atuador] comando ignorado: JSON inválido (%s)\n", erro.c_str());
    return;
  }
  const char* acao = cmd["acao"] | "";
  if (strcmp(acao, "ligar") == 0) {
    int minutos = cmd["minutos"] | 10;
    if (minutos < 1) minutos = 1;
    ligarIrrigacao(minutos);
  } else if (strcmp(acao, "desligar") == 0) {
    desligarIrrigacao("comando");
  } else {
    Serial.printf("[atuador] ação desconhecida: '%s'\n", acao);
  }
}

// ---------------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------------
void conectarMqtt() {
  while (!mqtt.connected()) {
    String clientId = String("agrosmart-") + SENSOR_ID + "-" + String((uint32_t)esp_random(), HEX);
    Serial.printf("[mqtt] conectando a %s:%u como %s", MQTT_HOST, MQTT_PORTA, clientId.c_str());
    if (mqtt.connect(clientId.c_str())) {
      mqtt.subscribe(topicoAtuador.c_str(), 1);
      Serial.printf(" ok · publicando em %s · comandos em %s\n", topicoLeitura.c_str(), topicoAtuador.c_str());
    } else {
      Serial.printf(" falhou (rc=%d), nova tentativa em 3 s\n", mqtt.state());
      delay(3000);
    }
  }
}

void publicarLeitura() {
  float arTemperatura = dht.readTemperature();   // NAN se o sensor não respondeu
  float arUmidade = dht.readHumidity();
  char iso[25];
  instanteIso(iso, sizeof(iso));

  float umidadeSolo = lerUmidadeSolo();
  float tempSolo = lerTemperaturaSolo();
  float bateria = lerBateria();
  float lux = lerLuminosidade();
  int rssi = WiFi.RSSI();

  digitalWrite(PINO_LED_BATERIA, bateria < BATERIA_ALERTA_PCT ? HIGH : LOW);

  // Mesma ordem de chaves do contrato; o que o DHT não leu vai como null
  // (chave presente, valor nulo — é assim que o silver decide pela regra SEN-01).
  JsonDocument doc;
  doc["sensor_id"] = SENSOR_ID;
  doc["talhao_id"] = TALHAO_ID;
  doc["medido_em"] = iso;
  doc["umidade_solo_pct"] = umidadeSolo;
  if (isnan(tempSolo)) doc["temperatura_solo_c"] = nullptr; else doc["temperatura_solo_c"] = tempSolo;
  if (isnan(arTemperatura)) doc["temperatura_ar_c"] = nullptr; else doc["temperatura_ar_c"] = arredondar1(arTemperatura);
  if (isnan(arUmidade)) doc["umidade_ar_pct"] = nullptr; else doc["umidade_ar_pct"] = arredondar1(arUmidade);
  doc["bateria_pct"] = bateria;
  doc["rssi_dbm"] = rssi;
  doc["luminosidade_lux"] = lux;  // fora do contrato v1 — o gateway descarta

  char buffer[384];
  size_t n = serializeJson(doc, buffer, sizeof(buffer));
  bool ok = mqtt.publish(topicoLeitura.c_str(), buffer, n);

  sequencia++;
  Serial.printf("[%lu] %s %s\n", (unsigned long)sequencia, ok ? "pub" : "ERRO", buffer);
}

// ---------------------------------------------------------------------------
// Arduino
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("AgroSmart · estação de campo · contrato sensores.v1");

  pinMode(PINO_LED_IRRIGACAO, OUTPUT);
  pinMode(PINO_LED_BATERIA, OUTPUT);
  digitalWrite(PINO_LED_IRRIGACAO, LOW);
  digitalWrite(PINO_LED_BATERIA, LOW);
  analogReadResolution(12);
  dht.begin();

  topicoLeitura = String("agrosmart/sensores/") + SENSOR_ID + "/leitura";
  topicoAtuador = String("agrosmart/atuadores/") + TALHAO_ID + "/irrigacao";
  topicoEstado = topicoAtuador + "/estado";

  conectarWiFi();
  sincronizarRelogio();

  mqtt.setServer(MQTT_HOST, MQTT_PORTA);
  mqtt.setCallback(aoReceberComando);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(30);
  conectarMqtt();

  Serial.println("Gire os potenciômetros: esquerdo = umidade do solo, direito = bateria.");
  Serial.println("Clique no DHT22 para ajustar temperatura/umidade do ar; no LDR, a luz.");
  Serial.printf("Para ligar a irrigação publique em %s: {\"acao\":\"ligar\",\"minutos\":5}\n",
                topicoAtuador.c_str());
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) conectarWiFi();
  if (!mqtt.connected()) conectarMqtt();
  mqtt.loop();

  if (irrigacaoLigada && (long)(millis() - irrigacaoAteMs) >= 0) {
    desligarIrrigacao("tempo esgotado");
  }

  unsigned long agora = millis();
  if (agora - ultimaPublicacaoMs >= INTERVALO_MS || ultimaPublicacaoMs == 0) {
    ultimaPublicacaoMs = agora;
    publicarLeitura();
  }
}
