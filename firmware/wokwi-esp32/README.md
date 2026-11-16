# Estação de campo AgroSmart — ESP32 no Wokwi

Firmware da estação de sensoriamento do talhão **TLH-03** (sensor **SNS-0301**),
simulada no [Wokwi](https://wokwi.com) — simulador online de ESP32 com Wi-Fi real
(equivalente ao TinkerCad pedido no requisito 2, que não simula ESP32 nem rede).
O dispositivo publica por MQTT exatamente o payload do contrato
`contratos/sensores.v1.yaml`; o gateway `agrosmart coletar-mqtt` o transforma nos
micro-batches JSONL que o pipeline da Fase 5 já ingere.

## Arquivos

| Arquivo | O que é |
|---|---|
| `sketch.ino` | firmware: Wi-Fi, NTP (UTC), leitura dos sensores, publicação MQTT, atuador de irrigação |
| `diagram.json` | o circuito: ESP32 DevKit-C v4, DHT22, 2 potenciômetros, NTC, LDR, 2 LEDs |
| `libraries.txt` | bibliotecas do projeto: PubSubClient, ArduinoJson, DHT sensor library (Adafruit) e Adafruit Unified Sensor. No editor web (wokwi.com) o arquivo não é lido automaticamente: instale as quatro pelo **Library Manager** (botão +, buscar pelo nome); no VS Code (wokwi.toml) o arquivo é usado como está |
| `wokwi.toml` | só para Wokwi no VS Code / `wokwi-cli` (binário compilado localmente); dispensável no site |
| `captura-wokwi.png` | **captura exigida pela entrega** (ver abaixo) — tirar e salvar aqui |

## Abrir no wokwi.com (3 minutos)

1. Entre em <https://wokwi.com> → **New Project** → **ESP32** (template "ESP32 Arduino").
2. Na aba `sketch.ino`, apague o conteúdo e cole o `sketch.ino` deste diretório.
3. Na aba `diagram.json`, substitua pelo `diagram.json` daqui.
4. Na aba `libraries.txt` (crie com o `+` ao lado das abas, se não existir), cole o conteúdo de `libraries.txt`.
5. **▶ Start**. O monitor serial mostra a sequência: `[wifi] … ok`, `[ntp] … ok · 2026-…Z`,
   `[mqtt] … ok`, e a partir daí uma linha JSON a cada 5 s — a mesma que vai para o broker.

Não há credenciais: a rede `Wokwi-GUEST` não tem senha e `broker.hivemq.com:1883` aceita
conexão anônima. Se o HiveMQ estiver instável, troque `MQTT_HOST` por `test.mosquitto.org`.

## O que girar para simular cenários

| Cenário | Ação no Wokwi | Efeito no payload / no lake |
|---|---|---|
| **Seca** | gire o potenciômetro da esquerda (`pot_solo`) até perto do mínimo | `umidade_solo_pct` cai (ex.: 12 %) → FAD abaixo do limiar → o copiloto/automação recomenda irrigar |
| **Calor** | clique no DHT22 e arraste *temperature* para 36 °C, *humidity* para 30 % | `temperatura_ar_c` 36, `umidade_ar_pct` 30 → risco fitossanitário muda |
| **Solo quente** | clique no NTC e ajuste a temperatura | `temperatura_solo_c` acompanha (regra SEN-05: −10 a 70 °C) |
| **Bateria fraca** | gire o potenciômetro da direita (`pot_bateria`) abaixo de ~15 % | `bateria_pct` < 15 → **LED vermelho** acende; no silver, alerta SEN-10 (ordem de manutenção) |
| **Noite** | clique no LDR e reduza a luz | `luminosidade_lux` → 0 (campo fora do contrato v1; só no serial/payload) |
| **Irrigação** | publique `{"acao":"ligar","minutos":5}` em `agrosmart/atuadores/TLH-03/irrigacao` | **LED verde** acende; estado em `…/irrigacao/estado`; desliga sozinho ao fim dos minutos |

Para publicar o comando sem instalar nada: <https://www.hivemq.com/demos/websocket-client/>
(conectar em `broker.hivemq.com`, porta 8884 WSS ou 8000 WS) → *Publish* no tópico acima.
Com o mosquitto local: `mosquitto_pub -t agrosmart/atuadores/TLH-03/irrigacao -m '{"acao":"ligar","minutos":5}'`.
O comando `agrosmart automacao --publicar` (Fase 6, requisito 3) publica esse mesmo comando a partir do gold.

## Ver as leituras chegando no pipeline

Em um terminal da máquina de desenvolvimento, apontando o gateway ao mesmo broker:

```bash
python -m agrosmart coletar-mqtt --broker broker.hivemq.com --topico "agrosmart/sensores/+/leitura" --janela-min 15 --duracao-s 120
python -m agrosmart ingerir --fonte sensores
python -m agrosmart processar --camada silver --fonte sensores
```

O gateway grava `landing/sensores/<AAAAMMDD>/leituras_<AAAAMMDD>T<HHMM>Z.jsonl`
e registra que descartou a chave `luminosidade_lux`. Atenção: o tópico é público —
qualquer pessoa publicando em `agrosmart/sensores/#` no HiveMQ aparece no gateway.
Para uma demonstração fechada use o mosquitto local (`docker compose -f infra/docker-compose.yml up -d mosquitto`)
com o emulador `python -m agrosmart dispositivo`, ou aponte o ESP32 para `host.wokwi.internal` via `wokwigw`.

## Onde tirar a captura exigida

A entrega pede "captura da simulação". Com a simulação rodando no wokwi.com:

1. deixe visíveis, na mesma tela, o **circuito** (LEDs e potenciômetros) e o **monitor serial**
   com pelo menos uma linha JSON publicada (`[n] pub {...}`);
2. de preferência com um cenário provocado (ex.: bateria < 15 % com o LED vermelho aceso);
3. salve como `firmware/wokwi-esp32/captura-wokwi.png`. É esse arquivo que
   `docs/fase6/02-coleta-iot.md` referencia.

Opcional: um segundo print do gateway (`coletar-mqtt`) recebendo a leitura do ESP32
pelo broker público, lado a lado com o monitor serial, fecha o fluxo dispositivo → lake.

## Notas de engenharia

* Só pinos **ADC1** (32–39) são usados para analógico: o ADC2 do ESP32 é desligado quando o Wi-Fi está ativo.
* O relógio é sincronizado por NTP **antes** da primeira publicação e `medido_em` sai em UTC
  (`…Z`): sem isso a regra SEN-08 (não-futuro) reprovaria as leituras.
* A chave `luminosidade_lux` é publicada de propósito fora do contrato v1 para mostrar o gateway
  protegendo o schema na porta. Entra no contrato v2 (roadmap), junto com `ph` e `condutividade_ds_m`
  reais — hoje esses dois campos opcionais saem como `null` porque a estação não tem as sondas.
* Em campo o intervalo é de 15 min (`INTERVALO_MS = 900000`) com o ESP32 em *deep sleep* entre leituras;
  na demonstração são 5 s para a janela encher rápido.
