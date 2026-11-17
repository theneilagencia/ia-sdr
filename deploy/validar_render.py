#!/usr/bin/env python3
"""Confere o `render.yaml` sem precisar de conta no Render.

Não substitui o `render blueprints validate` da CLI oficial — aquele conhece o
esquema do fornecedor e a validade dos planos. Este confere o que a CLI não sabe
e o que, errado, só apareceria como um deploy quebrado:

* **Referência para recurso que não existe.** `fromDatabase: ia-sdr-bd` num
  arquivo que define `ia-sdr-db` sincroniza e depois falha, com a variável vazia.
* **Variável que a aplicação exige e o blueprint não entrega.** A API recusa
  subir sem `SECRETS_ENCRYPTION_KEY`; descobrir isso no primeiro deploy é perder
  um ciclo inteiro de build.
* **`generation: off` sem aspas.** YAML lê `off` como o booleano falso, e o
  Render então não reconhece o valor. É o tipo de erro que se olha dez vezes sem
  ver.
* **Região diferente entre serviços.** Rede privada e `fromService` só funcionam
  dentro da mesma região, e o sintoma é "conexão recusada" muito depois.
* **Caminho de Dockerfile que não existe** no repositório.

    python3 deploy/validar_render.py [caminho/para/render.yaml]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent

#: Aceita um caminho no argumento para que dê para apontá-lo a uma cópia
#: adulterada — é assim que se confere que esta verificação verifica algo. Os
#: caminhos de Dockerfile continuam resolvidos contra a raiz do repositório.
ARQUIVO = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "render.yaml"

TIPOS = {"web", "pserv", "worker", "cron", "keyvalue", "redis"}
RUNTIMES = {"node", "python", "go", "ruby", "rust", "elixir", "docker", "image", "static"}

#: O que a aplicação exige para subir em produção, e por quê. Vem de
#: `app/core/config.py` (os campos) e de `app/core/startup.py` (o que é fatal).
#: Duplicado aqui de propósito: o blueprint é o único lugar onde a ausência de
#: uma delas não dá erro nenhum até o container já estar rodando.
EXIGIDAS_BACKEND = {
    "ENVIRONMENT": "sem isto as verificações de produção nem rodam",
    "DATABASE_URL": "é o role sem privilégio; é ele que faz o RLS valer",
    "DATABASE_ADMIN_URL": "autenticação, painel da plataforma e worker passam por ele",
    "APP_DB_USER": "o role de aplicação que o bootstrap cria",
    "APP_DB_PASSWORD": "idem",
    "JWT_SECRET": "sem isto a API recusa subir em produção",
    "SECRETS_ENCRYPTION_KEY": "cifra credencial de email e chave de API das empresas",
    "PUBLIC_BASE_URL": "base do link de descadastro de todo email",
    "APP_BASE_URL": "base do link de convite",
}

#: Serviços que rodam a imagem do backend, e portanto precisam de tudo acima.
#: Identificados pelo contexto de build, não pelo nome: renomear um serviço não
#: deveria fazer esta verificação parar de valer em silêncio.
CONTEXTO_BACKEND = "./backend"

problemas: list[str] = []


def erro(mensagem: str) -> None:
    problemas.append(mensagem)


def chaves_de(envvars: list[dict], grupos: dict[str, set[str]]) -> set[str]:
    """Nomes de variável que o serviço recebe, contando o que vem de grupo."""
    nomes: set[str] = set()
    for entrada in envvars:
        if "fromGroup" in entrada:
            nomes |= grupos.get(entrada["fromGroup"], set())
        elif "key" in entrada:
            nomes.add(entrada["key"])
    return nomes


def main() -> int:
    if not ARQUIVO.is_file():
        print(f"✗ {ARQUIVO} não existe")
        return 1

    dados = yaml.safe_load(ARQUIVO.read_text(encoding="utf-8"))
    if not isinstance(dados, dict):
        print("✗ render.yaml não é um mapeamento YAML")
        return 1

    servicos = dados.get("services") or []
    bancos = dados.get("databases") or []
    grupos_lista = dados.get("envVarGroups") or []

    grupos = {
        g["name"]: {e["key"] for e in g.get("envVars", []) if "key" in e} for g in grupos_lista
    }
    nomes_de_servico = {s["name"]: s.get("type") for s in servicos}
    nomes_de_banco = {b["name"] for b in bancos}

    # `off` sem aspas vira False, e o Render não reconhece um booleano aqui.
    previews = dados.get("previews") or {}
    if "generation" in previews and not isinstance(previews["generation"], str):
        erro(
            "previews.generation virou "
            f"{previews['generation']!r}: em YAML, `off` sem aspas é o booleano "
            'falso. Escreva "off".'
        )

    for grupo in grupos_lista:
        for entrada in grupo.get("envVars", []):
            for proibida in ("sync", "fromDatabase", "fromService", "fromGroup"):
                if proibida in entrada:
                    erro(
                        f"grupo '{grupo['name']}', variável "
                        f"'{entrada.get('key', '?')}': `{proibida}` não vale em grupo de "
                        "variáveis. `sync: false` em grupo é ignorado em silêncio — o "
                        "segredo chegaria vazio."
                    )
            # Num grupo só sobram duas origens possíveis, e precisa ser uma.
            # Duas é ambiguidade que o Render resolve do jeito dele, não do seu.
            fontes = [c for c in ("value", "generateValue") if c in entrada]
            if len(fontes) != 1:
                erro(
                    f"grupo '{grupo['name']}', variável '{entrada.get('key', '?')}': "
                    f"precisa de exatamente uma origem de valor, e tem {len(fontes)} "
                    f"({fontes})"
                )

    regioes = {s.get("region") for s in servicos if s.get("region")}
    regioes |= {b.get("region") for b in bancos if b.get("region")}
    if len(regioes) > 1:
        erro(
            f"há mais de uma região no arquivo ({', '.join(sorted(regioes))}): rede "
            "privada e `fromService` só funcionam dentro da mesma região."
        )

    for servico in servicos:
        nome = servico.get("name", "?")
        tipo = servico.get("type")
        if tipo not in TIPOS:
            erro(f"serviço '{nome}': type '{tipo}' não é um tipo do Render ({sorted(TIPOS)})")
        runtime = servico.get("runtime")
        if runtime is not None and runtime not in RUNTIMES:
            erro(f"serviço '{nome}': runtime '{runtime}' não existe no Render")

        if servico.get("healthCheckPath") and tipo != "web":
            erro(
                f"serviço '{nome}': healthCheckPath só vale para serviço web; "
                f"num '{tipo}' é ignorado, e quem escreveu acha que tem verificação."
            )

        for campo in ("dockerfilePath", "dockerContext"):
            caminho = servico.get(campo)
            if caminho and not (RAIZ / caminho).exists():
                erro(f"serviço '{nome}': {campo} '{caminho}' não existe no repositório")

        for entrada in servico.get("envVars", []):
            chave = entrada.get("key", entrada.get("fromGroup", "?"))
            if "fromGroup" in entrada:
                if entrada["fromGroup"] not in grupos:
                    erro(
                        f"serviço '{nome}': fromGroup '{entrada['fromGroup']}' não está "
                        "definido em envVarGroups"
                    )
                continue
            if "fromDatabase" in entrada:
                alvo = entrada["fromDatabase"].get("name")
                if alvo not in nomes_de_banco:
                    erro(
                        f"serviço '{nome}', variável '{chave}': fromDatabase aponta para "
                        f"'{alvo}', que não está em databases"
                    )
            if "fromService" in entrada:
                ref = entrada["fromService"]
                alvo = ref.get("name")
                if alvo not in nomes_de_servico:
                    erro(
                        f"serviço '{nome}', variável '{chave}': fromService aponta para "
                        f"'{alvo}', que não é um serviço deste arquivo"
                    )
                elif ref.get("type") and ref["type"] != nomes_de_servico[alvo]:
                    erro(
                        f"serviço '{nome}', variável '{chave}': fromService diz que "
                        f"'{alvo}' é '{ref['type']}', mas ele é "
                        f"'{nomes_de_servico[alvo]}'"
                    )
            fontes = [
                c
                for c in ("value", "generateValue", "sync", "fromDatabase", "fromService")
                if c in entrada
            ]
            if len(fontes) != 1:
                erro(
                    f"serviço '{nome}', variável '{chave}': precisa de exatamente uma "
                    f"origem de valor, e tem {len(fontes)} ({fontes})"
                )

    # O coração: a imagem do backend recebe tudo o que ela exige para subir.
    do_backend = [s for s in servicos if s.get("dockerContext") == CONTEXTO_BACKEND]
    if not do_backend:
        erro(
            f"nenhum serviço com dockerContext '{CONTEXTO_BACKEND}': ou o caminho mudou, "
            "ou a API e o worker saíram do arquivo."
        )
    for servico in do_backend:
        recebidas = chaves_de(servico.get("envVars", []), grupos)
        for exigida, porque in EXIGIDAS_BACKEND.items():
            if exigida not in recebidas:
                erro(f"serviço '{servico['name']}' não recebe {exigida} — {porque}")

    web = [s for s in servicos if s.get("type") == "web"]
    for servico in web:
        if "API_URL" not in chaves_de(servico.get("envVars", []), grupos):
            erro(
                f"serviço web '{servico['name']}' não recebe API_URL: as telas são "
                "renderizadas no servidor e toda chamada passa por ela."
            )

    if problemas:
        print(f"✗ {len(problemas)} problema(s) no render.yaml:")
        for p in problemas:
            print(f"  - {p}")
        return 1

    print(
        f"✓ render.yaml: {len(servicos)} serviço(s), {len(bancos)} banco(s), "
        f"{len(grupos)} grupo(s) — referências, região, caminhos e variáveis exigidas conferem"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
