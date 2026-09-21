"""Leitura de lista em CSV.

Quem monta lista de prospecção exporta do LinkedIn Sales Navigator, do Apollo,
de uma planilha do Excel ou de um CRM antigo. Cada um chama as colunas de um
jeito, metade sai com ponto e vírgula porque o Excel em português usa isso, e
uma parte vem em Latin-1. Exigir um formato exato seria transferir esse
trabalho para quem já tem a lista pronta.

O que este módulo **não** faz é adivinhar. Coluna que não bate com nenhum
apelido conhecido é ignorada, e linha sem empresa ou sem nome é reportada com o
número da linha — não descartada em silêncio. Import que diz "42 importados" e
engole oito linhas é pior do que import que falha.
"""

from __future__ import annotations

import csv
import io

from pydantic import ValidationError

from app.api.v1 import schemas
from app.core.errors import AppError

#: Apelidos por campo, em português e inglês, normalizados. A lista cobre o que
#: sai das ferramentas usadas na prática; o resto é ignorado sem drama.
APELIDOS: dict[str, tuple[str, ...]] = {
    "company_name": (
        "company_name",
        "company",
        "empresa",
        "conta",
        "account",
        "organization",
        "organizacao",
        "razao_social",
        "nome_da_empresa",
    ),
    "company_domain": (
        "company_domain",
        "domain",
        "website",
        "site",
        "dominio",
        "url",
        "company_website",
    ),
    "industry": ("industry", "setor", "segmento", "industria", "vertical"),
    "country": ("country", "pais", "país", "location_country"),
    "employee_count": (
        "employee_count",
        "employees",
        "funcionarios",
        "funcionários",
        "headcount",
        "porte",
        "num_funcionarios",
    ),
    "full_name": ("full_name", "name", "nome", "contato", "contact_name", "lead", "nome_completo"),
    "email": ("email", "e-mail", "email_address", "work_email", "endereco_de_email"),
    "title": ("title", "cargo", "job_title", "position", "funcao", "função"),
    "persona": ("persona", "buyer_persona", "perfil"),
    "linkedin_url": ("linkedin_url", "linkedin", "perfil_linkedin", "linkedin_profile"),
}

#: Teto de linhas por arquivo. O import em lote já tem limite de 500 itens por
#: chamada; este número é o mesmo, para a mensagem de erro ser uma só.
MAX_LINHAS = 500


class CsvInvalido(AppError):
    code = "invalid_csv"


def _normalizar(cabecalho: str) -> str:
    limpo = cabecalho.strip().lower().replace(" ", "_").replace("-", "_")
    return limpo.lstrip("﻿")


def _mapear_colunas(cabecalhos: list[str]) -> dict[str, str]:
    """Nome da coluna no arquivo -> campo do item de import."""
    mapa: dict[str, str] = {}
    for bruto in cabecalhos:
        normalizado = _normalizar(bruto or "")
        for campo, apelidos in APELIDOS.items():
            if normalizado in apelidos and campo not in mapa.values():
                mapa[bruto] = campo
                break
    return mapa


def _decodificar(bruto: bytes) -> str:
    try:
        return bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Planilha exportada do Excel em português costuma vir em Latin-1.
        # Recusar seria pedir para o usuário resolver encoding.
        return bruto.decode("latin-1")


def _dialeto(amostra: str) -> csv.Dialect | type[csv.Dialect]:
    try:
        return csv.Sniffer().sniff(amostra, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def ler(bruto: bytes) -> tuple[list[schemas.ProspectImportItem], list[dict]]:
    """Devolve (itens válidos, erros por linha).

    Os dois juntos, nunca um sem o outro: a resposta precisa dizer quantas
    linhas entraram **e** quais ficaram de fora, com o número da linha para a
    pessoa conseguir abrir o arquivo e corrigir.
    """
    texto = _decodificar(bruto)
    if not texto.strip():
        raise CsvInvalido("O arquivo está vazio.")

    leitor = csv.DictReader(io.StringIO(texto), dialect=_dialeto(texto[:4096]))
    if not leitor.fieldnames:
        raise CsvInvalido("Não foi possível ler o cabeçalho do arquivo.")

    mapa = _mapear_colunas(list(leitor.fieldnames))
    faltando = {"company_name", "full_name"} - set(mapa.values())
    if faltando:
        conhecidas = ", ".join(sorted({a for c in faltando for a in APELIDOS[c][:4]}))
        raise CsvInvalido(
            "O arquivo precisa de uma coluna com o nome da empresa e outra com o "
            f"nome da pessoa. Nomes aceitos, entre outros: {conhecidas}. "
            f"Colunas encontradas: {', '.join(leitor.fieldnames)}."
        )

    itens: list[schemas.ProspectImportItem] = []
    erros: list[dict] = []
    for numero, linha in enumerate(leitor, start=2):  # 1 é o cabeçalho
        if len(itens) >= MAX_LINHAS:
            erros.append(
                {
                    "line": numero,
                    "error": f"Arquivo acima de {MAX_LINHAS} linhas; divida em partes.",
                }
            )
            break

        dados = {}
        for coluna, campo in mapa.items():
            valor = (linha.get(coluna) or "").strip()
            if valor:
                dados[campo] = valor
        if not dados:
            continue  # linha em branco no fim do arquivo

        if "employee_count" in dados:
            # "1.200", "1,200" e "500+" aparecem o tempo todo em export de
            # ferramenta. O número é opcional: não vale perder a linha por ele.
            digitos = "".join(c for c in dados["employee_count"] if c.isdigit())
            if digitos:
                dados["employee_count"] = digitos
            else:
                dados.pop("employee_count")

        try:
            itens.append(schemas.ProspectImportItem.model_validate(dados))
        except ValidationError as exc:
            primeiro = exc.errors()[0]
            campo = ".".join(str(p) for p in primeiro.get("loc", ())) or "linha"
            erros.append({"line": numero, "error": f"{campo}: {primeiro.get('msg')}"})

    if not itens:
        detalhe = ""
        if erros:
            detalhe = f" Primeiro problema — linha {erros[0]['line']}: {erros[0]['error']}"
        raise CsvInvalido("Nenhuma linha válida no arquivo." + detalhe)
    return itens, erros
