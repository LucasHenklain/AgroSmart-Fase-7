"""Runner local do pipeline DevSecOps do AgroSmart (mesmos estágios do GitHub Actions).

Executa, na ordem, os estágios de ``.github/workflows/devsecops.yml`` com os mesmos
comandos e critérios de falha, e grava a evidência em
``outputs/pipeline/<AAAAMMDDTHHMMSS>/``:

* um ``NN-<estagio>.log`` por estágio (cada comando, código de saída e saída completa);
* artefatos: ``junit.xml``, ``coverage.xml``, ``cobertura.txt``, ``bandit.json``,
  ``bandit.sarif``, ``requisitos-runtime.txt``, ``pip-audit.json``, ``trivy.json``,
  ``trivy.sarif``;
* ``resumo.json`` + ``resumo.md`` (status, duração e números-chave), copiados também
  para ``outputs/pipeline/ultimo/``.

Só usa a biblioteca padrão. As ferramentas (ruff, pytest, bandit, pip-audit) rodam no
interpretador indicado em ``--python`` (padrão: o que executa este script); o build e o
scan da imagem usam o Docker (Trivy roda no container oficial ``aquasec/trivy``).

Rede com inspeção TLS: se existir uma cadeia de CA corporativa (``--ca-extra`` ou
``~/.claude/certs/corporate-ca-chain.pem``), o runner monta ``.certs/ca-bundle.pem``
(certifi + cadeia; pasta ignorada pelo git) e a exporta só para os processos filhos
(REQUESTS_CA_BUNDLE/SSL_CERT_FILE/PIP_CERT), para o build (secret ``ca_extra``) e para
o Trivy (montada no container).

Exemplos:

    python scripts/pipeline_local.py                      # todos os estágios
    python scripts/pipeline_local.py --estagios qualidade,testes,sast
    python scripts/pipeline_local.py --sem-docker         # pula build e scan da imagem

Código de saída: 0 se todos os estágios executados passaram; 1 caso contrário.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET  # noqa: N817 - só lê XML gerado pelas próprias ferramentas
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent

# --- Critérios (os mesmos do workflow; mude nos dois lugares) -------------------------
ALVOS_LINT = ["src", "tests", "app", "scripts"]
ALVOS_COMPLEXIDADE = ["src", "app", "scripts"]
COMPLEXIDADE_MAX = 30  # gate (McCabe por função)
COMPLEXIDADE_ALERTA = 20  # só informativo: lista as funções acima disso
COBERTURA_MIN = 70  # % combinado (linhas + ramos) de src/agrosmart; medido: 77%
ALVOS_BANDIT = ["src", "app"]
EXTRAS_RUNTIME = "app,iot,ia"
IMAGEM_CI = "agrosmart:ci"
TRIVY_VERSAO = "0.76.2"
TRIVY_SEVERIDADE_GATE = "CRITICAL"
ESTAGIOS = ["qualidade", "testes", "sast", "sca", "build", "imagem"]
ESTAGIOS_DOCKER = {"build", "imagem"}


@dataclass
class Resultado:
    estagio: str
    status: str = "pendente"  # passou | falhou | pulado
    duracao_s: float = 0.0
    log: str = ""
    numeros: dict[str, Any] = field(default_factory=dict)
    motivo: str = ""


class Estagio:
    """Contexto de um estágio: escreve o log e roda comandos registrando tudo."""

    def __init__(self, nome: str, indice: int, pasta: Path, env: dict[str, str]) -> None:
        self.nome = nome
        self.pasta = pasta
        self.env = env
        self.resultado = Resultado(estagio=nome, log=f"{indice:02d}-{nome}.log")
        self._log = (pasta / self.resultado.log).open("w", encoding="utf-8", newline="\n")
        self._log.write(f"# estágio: {nome}\n# início: {datetime.now().isoformat()}\n\n")
        self.falhas: list[str] = []

    def rodar(
        self, cmd: list[str], *, gate: bool = True, rotulo: str = "", timeout: int = 3600
    ) -> subprocess.CompletedProcess[str]:
        """Roda ``cmd`` na raiz do projeto. Se ``gate`` e o código != 0, o estágio falha."""
        exibido = " ".join(_citar(c) for c in cmd)
        self._log.write(f"$ {exibido}\n")
        self._log.flush()
        print(f"  [{self.nome}] $ {exibido}", flush=True)
        inicio = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 - comandos fixos montados pelo próprio runner
                cmd,
                cwd=RAIZ,
                env=self.env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as erro:
            proc = subprocess.CompletedProcess(cmd, 127, "", f"comando não encontrado: {erro}\n")
        except subprocess.TimeoutExpired as erro:
            proc = subprocess.CompletedProcess(cmd, 124, "", f"tempo esgotado: {erro}\n")
        duracao = time.monotonic() - inicio
        saida = (proc.stdout or "") + (proc.stderr or "")
        self._log.write(saida if saida.endswith("\n") or not saida else saida + "\n")
        self._log.write(f"# código de saída: {proc.returncode} ({duracao:.1f}s)\n\n")
        self._log.flush()
        if gate and proc.returncode != 0:
            self.falhas.append(rotulo or exibido)
        return proc

    def nota(self, texto: str) -> None:
        self._log.write(f"# {texto}\n")
        self._log.flush()

    def falhar(self, motivo: str) -> None:
        self.falhas.append(motivo)
        self.nota(f"FALHA: {motivo}")

    def fechar(self) -> None:
        status = "FALHOU" if self.falhas else "PASSOU"
        self._log.write(f"# resultado: {status}\n")
        for falha in self.falhas:
            self._log.write(f"#   - {falha}\n")
        self._log.close()


def _citar(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


# --- Estágios --------------------------------------------------------------------------


def estagio_qualidade(e: Estagio, py: str) -> None:
    e.rodar([py, "-m", "ruff", "--version"], gate=False)
    e.rodar([py, "-m", "ruff", "check", *ALVOS_LINT], rotulo="ruff check (lint)")
    e.rodar(
        [py, "-m", "ruff", "format", "--check", *ALVOS_LINT],
        rotulo="ruff format --check (formatação)",
    )
    e.rodar(
        [
            py,
            "-m",
            "ruff",
            "check",
            *ALVOS_COMPLEXIDADE,
            "--select",
            "C901",
            "--config",
            f"lint.mccabe.max-complexity={COMPLEXIDADE_MAX}",
        ],
        rotulo=f"complexidade ciclomática > {COMPLEXIDADE_MAX}",
    )
    alerta = e.rodar(
        [
            py,
            "-m",
            "ruff",
            "check",
            *ALVOS_COMPLEXIDADE,
            "--select",
            "C901",
            "--config",
            f"lint.mccabe.max-complexity={COMPLEXIDADE_ALERTA}",
            "--output-format",
            "concise",
            "--exit-zero",
        ],
        gate=False,
    )
    acima = [linha for linha in alerta.stdout.splitlines() if "C901" in linha]
    e.resultado.numeros["funcoes_complexidade_acima_alerta"] = len(acima)
    e.resultado.numeros["complexidade_alerta"] = COMPLEXIDADE_ALERTA
    e.resultado.numeros["complexidade_max_gate"] = COMPLEXIDADE_MAX


def estagio_testes(e: Estagio, py: str) -> None:
    pasta = e.pasta
    proc = e.rodar(
        [
            py,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--cov=agrosmart",
            "--cov-branch",
            f"--cov-report=xml:{pasta / 'coverage.xml'}",
            "--cov-report=term",
            f"--junitxml={pasta / 'junit.xml'}",
            f"--cov-fail-under={COBERTURA_MIN}",
        ],
        rotulo=f"pytest + cobertura >= {COBERTURA_MIN}%",
    )
    linhas_cov = [ln for ln in proc.stdout.splitlines() if ln.startswith(("Name", "TOTAL", "---"))]
    (pasta / "cobertura.txt").write_text(
        "\n".join(_tabela_cobertura(proc.stdout)) or "\n".join(linhas_cov), encoding="utf-8"
    )
    e.resultado.numeros.update(_ler_junit(pasta / "junit.xml"))
    e.resultado.numeros.update(_ler_cobertura(pasta / "coverage.xml"))
    e.resultado.numeros["cobertura_minima_pct"] = COBERTURA_MIN


def estagio_sast(e: Estagio, py: str) -> None:
    pasta = e.pasta
    base = [py, "-m", "bandit", "-r", *ALVOS_BANDIT, "-c", "pyproject.toml"]
    e.rodar([py, "-m", "bandit", "--version"], gate=False)
    # Relatório completo (todas as severidades) — evidência, não gate.
    e.rodar([*base, "-f", "json", "-o", str(pasta / "bandit.json"), "--exit-zero"], gate=False)
    e.rodar([*base, "-f", "sarif", "-o", str(pasta / "bandit.sarif"), "--exit-zero"], gate=False)
    # Gate: qualquer achado de severidade HIGH (com qualquer confiança) reprova.
    e.rodar([*base, "--severity-level", "high", "-f", "txt"], rotulo="bandit severidade HIGH")
    e.resultado.numeros.update(_ler_bandit(pasta / "bandit.json"))


def estagio_sca(e: Estagio, py: str) -> None:
    pasta = e.pasta
    requisitos = pasta / "requisitos-runtime.txt"
    e.rodar(
        [
            py,
            str(RAIZ / "scripts" / "requisitos_auditoria.py"),
            str(requisitos),
            "--extras",
            EXTRAS_RUNTIME,
        ],
        rotulo="gerar fecho de dependências de runtime",
    )
    e.rodar([py, "-m", "pip_audit", "--version"], gate=False)
    cmd = [
        py,
        "-m",
        "pip_audit",
        "-r",
        str(requisitos),
        "--no-deps",
        "--disable-pip",
        "--progress-spinner",
        "off",
        "--desc",
        "off",
    ]
    for vuln in _ler_ignorados(RAIZ / ".pip-audit-ignore"):
        cmd += ["--ignore-vuln", vuln]
    e.rodar([*cmd, "-f", "json", "-o", str(pasta / "pip-audit.json")], rotulo="pip-audit")
    e.resultado.numeros.update(_ler_pip_audit(pasta / "pip-audit.json"))
    if requisitos.exists():
        n = sum(1 for ln in requisitos.read_text(encoding="utf-8").splitlines() if ln.strip())
        e.resultado.numeros["dependencias_auditadas"] = n


def estagio_build(e: Estagio, py: str, ca_extra: Path | None) -> None:
    del py
    e.rodar(["docker", "version", "--format", "{{.Server.Version}}"], gate=False)
    cmd = ["docker", "buildx", "build", "--load", "--progress", "plain", "-t", IMAGEM_CI]
    if ca_extra:
        cmd += ["--secret", f"id=ca_extra,src={ca_extra}"]
    proc = e.rodar([*cmd, "."], rotulo="docker buildx build", timeout=3600)
    if proc.returncode != 0:
        return
    tamanho = e.rodar(["docker", "image", "inspect", "-f", "{{.Size}}", IMAGEM_CI], gate=False)
    usuario = e.rodar(["docker", "image", "inspect", "-f", "{{.Config.User}}", IMAGEM_CI])
    if tamanho.returncode == 0 and tamanho.stdout.strip().isdigit():
        e.resultado.numeros["imagem_bytes"] = int(tamanho.stdout.strip())
        e.resultado.numeros["imagem_mb"] = round(int(tamanho.stdout.strip()) / 1e6, 1)
    e.resultado.numeros["imagem_usuario"] = usuario.stdout.strip()
    if usuario.stdout.strip() in ("", "0", "root", "0:0"):
        e.falhar("imagem roda como root")
    # Smoke tests: a CLI sobe, o diagnóstico roda e o runtime não tem pip.
    e.rodar(["docker", "run", "--rm", IMAGEM_CI, "versao"], rotulo="smoke: versao")
    e.rodar(
        ["docker", "run", "--rm", "-e", "AGROSMART_BACKEND=filesystem", IMAGEM_CI, "doctor"],
        rotulo="smoke: doctor (backend filesystem)",
    )
    e.rodar(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "AGROSMART_BACKEND=filesystem",
            "--entrypoint",
            "sh",
            IMAGEM_CI,
            "-c",
            "python -m agrosmart init && python -m agrosmart contratos",
        ],
        rotulo="smoke: init + contratos (backend filesystem)",
    )
    e.rodar(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            IMAGEM_CI,
            "-c",
            "import importlib.util,os,sys;"
            "print('uid', os.getuid(), 'pip', importlib.util.find_spec('pip') is not None);"
            "sys.exit(os.getuid() == 0 or importlib.util.find_spec('pip') is not None)",
        ],
        rotulo="smoke: não-root e sem pip",
    )


def estagio_imagem(e: Estagio, py: str, bundle: Path | None) -> None:
    del py
    pasta = e.pasta
    imagem_trivy = f"aquasec/trivy:{TRIVY_VERSAO}"
    if e.rodar(["docker", "image", "inspect", imagem_trivy], gate=False).returncode != 0:
        if e.rodar(["docker", "pull", imagem_trivy], gate=False).returncode != 0:
            imagem_trivy = "aquasec/trivy:latest"
            e.nota(f"usando {imagem_trivy} (tag fixada indisponível localmente)")
    cache = RAIZ / ".cache" / "trivy"
    cache.mkdir(parents=True, exist_ok=True)
    base = [
        "docker",
        "run",
        "--rm",
        "-v",
        "//var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{cache}:/root/.cache/trivy",
        "-v",
        f"{pasta}:/saida",
        "-v",
        f"{RAIZ / '.trivyignore'}:/cfg/.trivyignore:ro",
    ]
    if bundle:
        base += ["-v", f"{bundle}:/certs/ca.pem:ro", "-e", "SSL_CERT_FILE=/certs/ca.pem"]
    base.append(imagem_trivy)
    e.rodar([*base, "--version"], gate=False)
    comum = ["image", "--scanners", "vuln", "--ignorefile", "/cfg/.trivyignore"]
    # Relatório completo (todas as severidades, inclusive sem correção) — evidência.
    e.rodar(
        [*base, *comum, "--format", "json", "-o", "/saida/trivy.json", IMAGEM_CI],
        rotulo="trivy relatório completo",
        timeout=1800,
    )
    e.rodar(
        [
            *base,
            *comum,
            "--skip-db-update",
            "--format",
            "sarif",
            "-o",
            "/saida/trivy.sarif",
            IMAGEM_CI,
        ],
        gate=False,
    )
    # Gate: CRITICAL com correção disponível reprova.
    e.rodar(
        [
            *base,
            *comum,
            "--skip-db-update",
            "--severity",
            TRIVY_SEVERIDADE_GATE,
            "--ignore-unfixed",
            "--exit-code",
            "1",
            "--format",
            "table",
            IMAGEM_CI,
        ],
        rotulo=f"trivy {TRIVY_SEVERIDADE_GATE} corrigível",
    )
    e.resultado.numeros.update(_ler_trivy(pasta / "trivy.json"))


# --- Leitura dos artefatos -------------------------------------------------------------


def _ler_junit(caminho: Path) -> dict[str, Any]:
    if not caminho.exists():
        return {}
    raiz = ET.parse(caminho).getroot()  # noqa: S314 - arquivo gerado localmente pelo pytest
    suites = [raiz] if raiz.tag == "testsuite" else list(raiz.iter("testsuite"))
    tot = Counter()
    for s in suites:
        for chave in ("tests", "failures", "errors", "skipped"):
            tot[chave] += int(s.get(chave, 0))
        tot["tempo"] += float(s.get("time", 0))
    passados = tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"]
    return {
        "testes_total": tot["tests"],
        "testes_passados": passados,
        "testes_falhas": tot["failures"],
        "testes_erros": tot["errors"],
        "testes_pulados": tot["skipped"],
        "testes_tempo_s": round(tot["tempo"], 1),
    }


def _ler_cobertura(caminho: Path) -> dict[str, Any]:
    if not caminho.exists():
        return {}
    raiz = ET.parse(caminho).getroot()  # noqa: S314 - arquivo gerado localmente pelo coverage
    return {
        "cobertura_linhas_pct": round(float(raiz.get("line-rate", 0)) * 100, 2),
        "cobertura_ramos_pct": round(float(raiz.get("branch-rate", 0)) * 100, 2),
        "linhas_validas": int(raiz.get("lines-valid", 0)),
        "linhas_cobertas": int(raiz.get("lines-covered", 0)),
    }


def _tabela_cobertura(saida: str) -> list[str]:
    linhas = saida.splitlines()
    for i, linha in enumerate(linhas):
        if linha.startswith("Name") and "Stmts" in linha:
            fim = next((j for j in range(i, len(linhas)) if linhas[j].startswith("TOTAL")), i)
            return linhas[i : fim + 1]
    return []


def _ler_bandit(caminho: Path) -> dict[str, Any]:
    if not caminho.exists():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    sev = Counter(r["issue_severity"] for r in dados.get("results", []))
    regras = Counter(r["test_id"] for r in dados.get("results", []))
    totais = dados.get("metrics", {}).get("_totals", {})
    return {
        "bandit_high": sev.get("HIGH", 0),
        "bandit_medium": sev.get("MEDIUM", 0),
        "bandit_low": sev.get("LOW", 0),
        "bandit_por_regra": dict(sorted(regras.items())),
        "bandit_nosec_justificados": int(totais.get("skipped_tests", 0)),
        "bandit_linhas_analisadas": int(totais.get("loc", 0)),
    }


def _ler_pip_audit(caminho: Path) -> dict[str, Any]:
    if not caminho.exists():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    deps = dados.get("dependencies", [])
    vulneraveis = {d["name"]: sorted({v["id"] for v in d.get("vulns", [])}) for d in deps}
    vulneraveis = {k: v for k, v in vulneraveis.items() if v}
    return {
        "pip_audit_vulnerabilidades": sum(len(v) for v in vulneraveis.values()),
        "pip_audit_pacotes_vulneraveis": len(vulneraveis),
        "pip_audit_detalhe": {k: len(v) for k, v in sorted(vulneraveis.items())},
    }


def _ler_trivy(caminho: Path) -> dict[str, Any]:
    if not caminho.exists():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    sev: Counter[str] = Counter()
    sev_corrigivel: Counter[str] = Counter()
    vistos: set[tuple[str, str, str]] = set()
    for alvo in dados.get("Results", []) or []:
        for v in alvo.get("Vulnerabilities", []) or []:
            chave = (v["VulnerabilityID"], v["PkgName"], v.get("InstalledVersion", ""))
            if chave in vistos:
                continue
            vistos.add(chave)
            sev[v.get("Severity", "UNKNOWN")] += 1
            if v.get("FixedVersion"):
                sev_corrigivel[v.get("Severity", "UNKNOWN")] += 1
    os_info = dados.get("Metadata", {}).get("OS", {})
    ordem = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    return {
        "trivy_por_severidade": {s: sev.get(s, 0) for s in ordem},
        "trivy_corrigiveis_por_severidade": {s: sev_corrigivel.get(s, 0) for s in ordem},
        "trivy_total": sum(sev.values()),
        "imagem_so": f"{os_info.get('Family', '?')} {os_info.get('Name', '')}".strip(),
    }


def _ler_ignorados(caminho: Path) -> list[str]:
    if not caminho.exists():
        return []
    ids = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#", 1)[0].strip()
        if linha:
            ids.append(linha)
    return ids


# --- Ambiente --------------------------------------------------------------------------


def preparar_bundle(ca_extra: Path | None, py: str) -> Path | None:
    """certifi + cadeia corporativa em .certs/ca-bundle.pem (ignorado pelo git)."""
    if not ca_extra or not ca_extra.exists():
        return None
    certifi = subprocess.run(  # noqa: S603
        [py, "-c", "import certifi; print(certifi.where())"],
        capture_output=True,
        text=True,
        check=False,
    )
    destino = RAIZ / ".certs" / "ca-bundle.pem"
    destino.parent.mkdir(exist_ok=True)
    partes = []
    if certifi.returncode == 0 and Path(certifi.stdout.strip()).exists():
        partes.append(Path(certifi.stdout.strip()).read_text(encoding="ascii", errors="ignore"))
    partes.append(ca_extra.read_text(encoding="ascii", errors="ignore"))
    destino.write_text("\n".join(p.strip() for p in partes) + "\n", encoding="ascii")
    return destino


def montar_env(bundle: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "DOCKER_BUILDKIT": "1"}
    )
    if bundle:
        for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "PIP_CERT"):
            env[var] = str(bundle)
    return env


# --- Resumo ----------------------------------------------------------------------------


def escrever_resumo(pasta: Path, resultados: list[Resultado], meta: dict[str, Any]) -> None:
    geral = "falhou" if any(r.status == "falhou" for r in resultados) else "passou"
    resumo = {**meta, "status_geral": geral, "estagios": [asdict(r) for r in resultados]}
    (pasta / "resumo.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    simbolo = {"passou": "✅ passou", "falhou": "❌ falhou", "pulado": "⏭ pulado"}
    md = [
        f"# Pipeline DevSecOps (runner local) — {meta['execucao']}",
        "",
        f"- Status geral: **{geral.upper()}**",
        f"- Commit: `{meta['commit']}`{' (árvore com alterações)' if meta['sujo'] else ''}",
        f"- Host: {meta['host']} · Python {meta['python']} · duração {meta['duracao_s']} s",
        "",
        "| Estágio | Status | Duração (s) | Números-chave | Log |",
        "|---|---|---:|---|---|",
    ]
    for r in resultados:
        nums = "; ".join(f"{k}={_fmt(v)}" for k, v in r.numeros.items() if not isinstance(v, dict))
        if r.motivo:
            nums = (nums + "; " if nums else "") + f"falhas: {r.motivo}"
        md.append(
            f"| {r.estagio} | {simbolo.get(r.status, r.status)} | {r.duracao_s} | "
            f"{nums or '—'} | [{r.log}]({r.log}) |"
        )
    detalhes = [
        (r.estagio, k, v)
        for r in resultados
        for k, v in r.numeros.items()
        if isinstance(v, dict) and v
    ]
    if detalhes:
        md += ["", "## Detalhes", ""]
        for estagio, chave, valor in detalhes:
            itens = ", ".join(f"{k}: {v}" for k, v in valor.items())
            md.append(f"- **{estagio} · {chave}**: {itens}")
    (pasta / "resumo.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    ultimo = pasta.parent / "ultimo"
    ultimo.mkdir(exist_ok=True)
    for nome in ("resumo.json", "resumo.md"):
        shutil.copy2(pasta / nome, ultimo / nome)
    (ultimo / "ORIGEM.txt").write_text(
        f"Cópia do resumo de outputs/pipeline/{pasta.name}/ (logs e artefatos completos lá).\n",
        encoding="utf-8",
    )


def _fmt(valor: Any) -> str:
    return f"{valor:g}" if isinstance(valor, float) else str(valor)


def _git(*args: str) -> str:
    proc = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,  # noqa: S607
    )
    return proc.stdout.strip()


# --- Main ------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runner local do pipeline DevSecOps.")
    parser.add_argument(
        "--estagios",
        default=",".join(ESTAGIOS),
        help=f"lista separada por vírgula (padrão: {','.join(ESTAGIOS)})",
    )
    parser.add_argument("--sem-docker", action="store_true", help="pula build e imagem")
    parser.add_argument("--python", default=sys.executable, help="interpretador das ferramentas")
    parser.add_argument("--saida", type=Path, default=RAIZ / "outputs" / "pipeline")
    parser.add_argument(
        "--ca-extra",
        type=Path,
        default=Path(
            os.environ.get(
                "AGROSMART_CA_EXTRA", Path.home() / ".claude" / "certs" / "corporate-ca-chain.pem"
            )
        ),
        help="cadeia de CA corporativa (inspeção TLS); ignorada se não existir",
    )
    args = parser.parse_args(argv)

    pedidos = [s.strip() for s in args.estagios.split(",") if s.strip()]
    invalidos = [s for s in pedidos if s not in ESTAGIOS]
    if invalidos:
        parser.error(f"estágio(s) desconhecido(s): {invalidos}")

    carimbo = datetime.now().strftime("%Y%m%dT%H%M%S")
    pasta = (args.saida / carimbo).resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    ca_extra = args.ca_extra if args.ca_extra and args.ca_extra.exists() else None
    bundle = preparar_bundle(ca_extra, args.python)
    env = montar_env(bundle)
    py = args.python
    print(f"Pipeline DevSecOps local → {pasta}", flush=True)

    executores = {
        "qualidade": lambda e: estagio_qualidade(e, py),
        "testes": lambda e: estagio_testes(e, py),
        "sast": lambda e: estagio_sast(e, py),
        "sca": lambda e: estagio_sca(e, py),
        "build": lambda e: estagio_build(e, py, ca_extra),
        "imagem": lambda e: estagio_imagem(e, py, bundle),
    }
    resultados: list[Resultado] = []
    inicio_total = time.monotonic()
    build_ok = True
    for indice, nome in enumerate(ESTAGIOS, start=1):
        if nome not in pedidos:
            continue
        if nome in ESTAGIOS_DOCKER and args.sem_docker:
            resultados.append(Resultado(nome, "pulado", motivo="--sem-docker"))
            continue
        if nome == "imagem" and not build_ok:
            resultados.append(Resultado(nome, "pulado", motivo="build falhou"))
            continue
        e = Estagio(nome, indice, pasta, env)
        inicio = time.monotonic()
        try:
            executores[nome](e)
        except Exception as erro:  # noqa: BLE001 - o runner registra e segue para o próximo
            e.falhar(f"erro interno do runner: {erro!r}")
        e.resultado.duracao_s = round(time.monotonic() - inicio, 1)
        e.resultado.status = "falhou" if e.falhas else "passou"
        e.resultado.motivo = " | ".join(e.falhas)
        e.fechar()
        if nome == "build" and e.falhas:
            build_ok = False
        resultados.append(e.resultado)
        print(f"  => {nome}: {e.resultado.status.upper()} ({e.resultado.duracao_s}s)", flush=True)

    meta = {
        "execucao": carimbo,
        "pasta": str(pasta.relative_to(RAIZ)).replace("\\", "/"),
        "commit": _git("rev-parse", "--short", "HEAD"),
        "sujo": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "host": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "duracao_s": round(time.monotonic() - inicio_total, 1),
        "ca_corporativa": bool(bundle),
        "criterios": {
            "complexidade_max": COMPLEXIDADE_MAX,
            "cobertura_min_pct": COBERTURA_MIN,
            "bandit_gate": "severidade HIGH",
            "pip_audit_gate": "qualquer vulnerabilidade conhecida (exceto .pip-audit-ignore)",
            "trivy_gate": f"{TRIVY_SEVERIDADE_GATE} com correção (--ignore-unfixed)",
        },
    }
    escrever_resumo(pasta, resultados, meta)
    geral = "FALHOU" if any(r.status == "falhou" for r in resultados) else "PASSOU"
    print(f"Pipeline {geral} — resumo em {pasta / 'resumo.md'}", flush=True)
    return 1 if geral == "FALHOU" else 0


if __name__ == "__main__":
    raise SystemExit(main())
