"""Exportação dos dados de um tenant.

Existe por dois motivos, e o segundo é o que importa: a LGPD dá ao cliente o
direito de levar os dados dele embora, e uma plataforma da qual não se sai é
uma plataforma na qual não se entra. Quem avalia contratar pergunta "e se eu
quiser sair?" — e "abre um chamado" é uma resposta pior do que um endpoint.

Duas regras:

* **Nada de segredo.** Integrações aparecem pelo provedor e pela data; a chave
  da Anthropic e a senha de email do cliente não são serializadas aqui nem em
  lugar nenhum. Exportar credencial cifrada não ajudaria quem exporta e criaria
  uma cópia a mais do que já é o ativo mais sensível da plataforma.
* **Nada de outro tenant.** A leitura inteira passa pela sessão com escopo, e
  é o RLS que garante — não um `WHERE` que alguém pode esquecer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.ai import AgentRun, Integration
from app.db.models.engagement import (
    Conversation,
    Meeting,
    Message,
    Qualification,
    Sequence,
    SequenceEnrollment,
)
from app.db.models.knowledge import CompanyProfile, KnowledgeChunk, KnowledgeDocument
from app.db.models.platform import AuditLog, Tenant, UsageEvent
from app.db.models.sales import Campaign, Company, Contact, Prospect, Research, Score

#: Teto por coleção. Uma exportação que derruba o processo não serve de saída
#: para ninguém; quando o teto é atingido, a resposta diz.
MAX_LINHAS = 20_000

#: Campos que nunca saem, esteja onde estiver — por nome exato.
#: `config`, em integração, guarda a dica do token e a configuração da conexão.
CAMPOS_PROIBIDOS = {"config"}

#: E por pedaço de nome, que é o que faltava: a coluna do segredo cifrado chama-se
#: `credentials_encrypted`, não `credentials`, e passava pelo filtro de nome exato
#: sem que nada reclamasse. O teste que existia conferia a ausência da chave em
#: texto claro — que de fato não estava lá, porque está cifrada — e por isso o
#: blob saía na exportação enquanto o módulo prometia o contrário.
#:
#: "token" **não** entra na lista de propósito: `input_tokens`, `output_tokens`,
#: `max_output_tokens` e `token_count` são contadores, e são exatamente o que o
#: cliente precisa levar para auditar o próprio consumo.
MARCAS_DE_SEGREDO = ("credential", "secret", "password", "api_key", "token_hint")


def _e_segredo(nome: str) -> bool:
    minusculo = nome.lower()
    return minusculo in CAMPOS_PROIBIDOS or any(m in minusculo for m in MARCAS_DE_SEGREDO)


def _valor(bruto):
    if isinstance(bruto, datetime | date):
        return bruto.isoformat()
    if isinstance(bruto, uuid.UUID):
        return str(bruto)
    if isinstance(bruto, Decimal):
        return float(bruto)
    return bruto


def _linha(obj) -> dict:
    return {
        coluna.name: _valor(getattr(obj, coluna.name))
        for coluna in obj.__table__.columns
        if not _e_segredo(coluna.name)
    }


def _coletar(session: Session, modelo, ordem=None) -> tuple[list[dict], bool]:
    stmt = select(modelo).limit(MAX_LINHAS + 1)
    if ordem is not None:
        stmt = stmt.order_by(ordem)
    linhas = list(session.execute(stmt).scalars())
    truncado = len(linhas) > MAX_LINHAS
    return [_linha(o) for o in linhas[:MAX_LINHAS]], truncado


#: O que a exportação leva, declarado no módulo e não escondido dentro da
#: função: é a lista que uma verificação pode percorrer para provar que nenhuma
#: coluna de segredo sai — inclusive uma adicionada amanhã.
COLECOES = {
    "company_profile": CompanyProfile,
    "campaigns": Campaign,
    "sequences": Sequence,
    "sequence_enrollments": SequenceEnrollment,
    "companies": Company,
    "contacts": Contact,
    "prospects": Prospect,
    "research": Research,
    "scores": Score,
    "conversations": Conversation,
    "messages": Message,
    "qualifications": Qualification,
    "meetings": Meeting,
    "knowledge_documents": KnowledgeDocument,
    "knowledge_chunks": KnowledgeChunk,
    "integrations": Integration,
    "agent_runs": AgentRun,
    "usage_events": UsageEvent,
    "audit_logs": AuditLog,
}


def export_tenant(session: Session, tenant_id: uuid.UUID) -> dict:
    """Tudo o que é deste tenant, menos os segredos dele."""
    tenant = session.get(Tenant, tenant_id)

    dados: dict[str, list[dict]] = {}
    truncadas: list[str] = []
    for nome, modelo in COLECOES.items():
        linhas, truncado = _coletar(session, modelo)
        dados[nome] = linhas
        if truncado:
            truncadas.append(nome)

    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "tenant": {
            "id": str(tenant_id),
            "name": tenant.name if tenant else None,
            "slug": tenant.slug if tenant else None,
            "plan": tenant.plan if tenant else None,
        },
        "counts": {nome: len(linhas) for nome, linhas in dados.items()},
        #: Quais coleções bateram no teto — sem isso, uma exportação
        #: incompleta passaria por completa.
        "truncated": truncadas,
        "row_limit_per_collection": MAX_LINHAS,
        # O que ficou de fora, dito no pacote: exportação que omite em silêncio
        # faz o cliente acreditar que levou tudo.
        "excluded": sorted(CAMPOS_PROIBIDOS) + [f"*{m}*" for m in MARCAS_DE_SEGREDO],
        "data": dados,
    }
