"""Experimento de falhas controlado (Fase 7): derruba e religa dependências do worker.

    python scripts/injetar_falhas.py --rotulo v1 --atraso 480 --queda 150 --intervalo 360

Sequência: espera ``--atraso`` s → para o MinIO (Data Lake) por ``--queda`` s → religa →
espera ``--intervalo`` s → para o Mosquitto (broker MQTT) por ``--queda`` s → religa.
Cada ação é registrada com horário UTC em ``outputs/prints/fase7-05-injecao-falhas-<rotulo>.txt``.

É o que dá variação real ao event log da mineração de processos (ciclos com falha,
retentativa, recuperação) e o que dispara os alertas do Prometheus. Rotulado como
experimento: as quedas são provocadas, não acidentes. Só mexe nos containers
``agrosmart-minio`` e ``agrosmart-mosquitto``.
"""

from __future__ import annotations

import argparse
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ALVOS = {"minio": "agrosmart-minio", "mosquitto": "agrosmart-mosquitto"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotulo", required=True)
    parser.add_argument("--atraso", type=int, default=480)
    parser.add_argument("--queda", type=int, default=150)
    parser.add_argument("--intervalo", type=int, default=360)
    parser.add_argument("--alvos", nargs="*", default=["minio", "mosquitto"], choices=list(ALVOS))
    args = parser.parse_args()

    registro = RAIZ / "outputs" / "prints" / f"fase7-05-injecao-falhas-{args.rotulo}.txt"
    registro.parent.mkdir(parents=True, exist_ok=True)

    def anotar(texto: str) -> None:
        linha = f"{datetime.now(UTC).isoformat(timespec='seconds')}  {texto}"
        print(linha, flush=True)
        with registro.open("a", encoding="utf-8") as arquivo:
            arquivo.write(linha + "\n")

    def docker(*comando: str) -> None:
        r = subprocess.run(["docker", *comando], capture_output=True, text=True, check=False)  # noqa: S603,S607
        anotar(f"$ docker {' '.join(comando)} -> rc={r.returncode} {r.stdout.strip()}")

    anotar(f"experimento '{args.rotulo}': atraso {args.atraso}s, queda {args.queda}s, alvos {args.alvos}")
    time.sleep(args.atraso)
    for i, alvo in enumerate(args.alvos):
        if i:
            time.sleep(args.intervalo)
        anotar(f"QUEDA {alvo}: parando {ALVOS[alvo]}")
        docker("stop", ALVOS[alvo])
        time.sleep(args.queda)
        anotar(f"RETORNO {alvo}: iniciando {ALVOS[alvo]}")
        docker("start", ALVOS[alvo])
    anotar("experimento concluído")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
