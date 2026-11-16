# -*- coding: utf-8 -*-
"""Abre o Wokwi no Edge (DevTools Protocol), carrega sketch.ino + diagram.json, instala as bibliotecas pelo Library Manager,
inicia a simulação do ESP32 e salva uma captura em PNG — a "captura da simulação" exigida na Fase 6, reproduzível.

Uso:  python capturar_wokwi.py [--saida captura-wokwi.png] [--espera 75] [--janela]
      --janela  abre o Edge visível (padrão: headless). Requer: Edge, pacote websocket-client, internet (wokwi.com e broker MQTT).
Enquanto roda, em outro terminal:  python -m agrosmart coletar-mqtt --broker broker.hivemq.com --topico "agrosmart/sensores/+/leitura" --janela-min 1 --duracao-s 60
"""
import argparse, base64, json, os, subprocess, sys, tempfile, time, urllib.request

try:
    import websocket  # websocket-client
except ImportError:
    sys.exit("instale: python -m pip install websocket-client")

AQUI = os.path.dirname(os.path.abspath(__file__))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BIBLIOTECAS = ["PubSubClient", "ArduinoJson", "DHT sensor library", "Adafruit Unified Sensor"]

ap = argparse.ArgumentParser()
ap.add_argument("--saida", default=os.path.join(AQUI, "captura-wokwi.png"))
ap.add_argument("--espera", type=int, default=75, help="segundos de simulação antes da captura")
ap.add_argument("--janela", action="store_true"); ap.add_argument("--porta", type=int, default=9333)
args = ap.parse_args()

S = open(os.path.join(AQUI, "sketch.ino"), encoding="utf-8").read()
D = open(os.path.join(AQUI, "diagram.json"), encoding="utf-8").read()

perfil = tempfile.mkdtemp(prefix="edge_wokwi_")
cmd = [EDGE, f"--remote-debugging-port={args.porta}", "--remote-allow-origins=*", f"--user-data-dir={perfil}", "--window-size=1900,1000",
       "--no-first-run", "--disable-extensions", "--disable-gpu", "https://wokwi.com/projects/new/esp32"]
if not args.janela: cmd.insert(1, "--headless=new")
env = {k: v for k, v in os.environ.items() if not k.startswith(("CHROME_", "CLAUDE", "ELECTRON_"))}
edge = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def alvo():
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.porta}/json", timeout=2) as r:
                for t in json.load(r):
                    if t.get("type") == "page" and "wokwi" in t.get("url", ""): return t["webSocketDebuggerUrl"]
        except Exception: pass
        time.sleep(0.5)
    raise SystemExit("Edge não expôs a página do Wokwi via DevTools")

import atexit
atexit.register(lambda: edge.poll() is None and edge.terminate())   # encerra o Edge mesmo se algo falhar no meio
ws = websocket.create_connection(alvo(), timeout=120, suppress_origin=True); _id = [0]
def cdp(metodo, **params):
    _id[0] += 1; ws.send(json.dumps({"id": _id[0], "method": metodo, "params": params}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id[0]:
            if "error" in m: raise RuntimeError(m["error"])
            return m.get("result", {})
def js(expr):
    r = cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
    return r.get("result", {}).get("value")

cdp("Page.enable"); cdp("Runtime.enable")
cdp("Emulation.setDeviceMetricsOverride", width=1900, height=1000, deviceScaleFactor=1, mobile=False)
print("aguardando o editor...")
for _ in range(120):
    if js("typeof window.monaco === 'object' && monaco.editor.getModels().some(m => m.uri.toString() === 'vfs:sketch.ino')"): break
    time.sleep(1)
else: raise SystemExit("editor do Wokwi não carregou")

js("window.__agro = {S: %s, D: %s}; 1" % (json.dumps(S), json.dumps(D)))
js("monaco.editor.getModels().find(m => m.uri.toString() === 'vfs:sketch.ino').setValue(window.__agro.S); 1")
js("""(async () => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'diagram.json'); b && b.click();
  await new Promise(r => setTimeout(r, 1000)); const m = monaco.editor.getModels().find(x => x.uri.toString() === 'vfs:diagram.json');
  if (m) m.setValue(window.__agro.D); return !!m; })()""")
print("sketch e diagrama carregados")
for lib in BIBLIOTECAS:
    ok = js("""(async (lib) => {
  const lm = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Library Manager'); lm && lm.click();
  await new Promise(r => setTimeout(r, 800));
  const add = [...document.querySelectorAll('button')].find(x => (x.getAttribute('aria-label') || x.title || '') === 'Add a new library'); add && add.click();
  await new Promise(r => setTimeout(r, 900));
  const inp = document.querySelector('input[type=text]'); if (!inp) return 'sem input';
  inp.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inp, lib); inp.dispatchEvent(new Event('input', {bubbles: true}));
  for (let i = 0; i < 20; i++) { await new Promise(r => setTimeout(r, 400));
    const item = [...document.querySelectorAll('[role=menuitem]')].find(e => e.textContent.trim() === lib); if (item) { item.click(); return 'ok'; } }
  return 'não encontrada'; })(%s)""" % json.dumps(lib))
    print(f"biblioteca {lib}: {ok}")
js("""(async () => { const s = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Simulation'); s && s.click();
  await new Promise(r => setTimeout(r, 800)); const p = [...document.querySelectorAll('button')].find(x => (x.getAttribute('aria-label') || x.title || '') === 'Start the simulation');
  p && p.click(); return !!p; })()""")
print(f"simulação iniciada; aguardando {args.espera} s (compilação + Wi-Fi + NTP + MQTT)...")
for i in range(args.espera):
    time.sleep(1)
    if i % 15 == 14:
        st = js("(document.body.innerText.match(/(Build failed|Error during build|fatal error)[^\\n]{0,120}/g) || []).join(' | ')")
        if st: raise SystemExit("falha de build: " + st)
js("""(async () => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'sketch.ino'); b && b.click();
  await new Promise(r => setTimeout(r, 1500)); return !!b; })()""")   # volta ao código: o monitor serial aparece embaixo do editor
estado = js("[...document.querySelectorAll('button')].map(b => b.getAttribute('aria-label') || '').filter(t => /simulation/i.test(t)).join(', ')")
png = base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"])
open(args.saida, "wb").write(png)
print(f"estado: {estado} · captura salva em {args.saida} ({len(png) // 1024} KB)")
try:
    ws.close(); edge.terminate()
except Exception:
    pass
