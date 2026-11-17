"""``/health``, ``/metrics`` e ``/status`` por HTTP, numa thread do worker.

Biblioteca padrão (``http.server``): três rotas de leitura não justificam um framework.
Só GET; nada aqui altera o estado do worker.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agrosmart.operacao.metricas import EstadoOperacao


def _handler_para(estado: EstadoOperacao) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "agrosmart-worker"
        sys_version = ""

        def _responder(self, codigo: int, corpo: str, tipo: str) -> None:
            dados = corpo.encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(dados)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(dados)

        def do_GET(self) -> None:  # noqa: N802 - nome exigido por BaseHTTPRequestHandler
            rota = self.path.split("?", 1)[0]
            if rota == "/metrics":
                self._responder(
                    200, estado.para_prometheus(), "text/plain; version=0.0.4; charset=utf-8"
                )
            elif rota == "/health":
                ok, motivo = estado.saude()
                corpo = json.dumps({"status": "ok" if ok else "degradado", "motivo": motivo})
                self._responder(200 if ok else 503, corpo, "application/json")
            elif rota in {"/status", "/"}:
                corpo = json.dumps(estado.para_dict(), ensure_ascii=False, default=str, indent=2)
                self._responder(200, corpo, "application/json; charset=utf-8")
            else:
                self._responder(404, '{"erro": "rota desconhecida"}', "application/json")

        def log_message(self, *_: object) -> None:  # o scrape a cada 15 s afogaria o log
            return

    return Handler


def iniciar_servidor(
    estado: EstadoOperacao, porta: int, host: str = "127.0.0.1"
) -> ThreadingHTTPServer:
    """Sobe o servidor numa thread daemon e devolve-o (``shutdown()`` para parar).

    Por padrão escuta só em localhost; no container o compose passa
    ``AGROSMART_METRICAS_HOST=0.0.0.0`` para o Prometheus alcançar pela rede interna
    (a porta não é publicada para fora além de 127.0.0.1).
    """
    servidor = ThreadingHTTPServer((host, porta), _handler_para(estado))
    threading.Thread(target=servidor.serve_forever, name="metricas", daemon=True).start()
    return servidor
