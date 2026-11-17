"""Base de conhecimento: ingestão, fatiamento e recuperação por relevância.

É o ativo mais sensível da plataforma — o que a IA sabe sobre o negócio de um
cliente — e por isso tudo aqui é escrito e lido dentro da sessão com escopo de
tenant. Nenhuma função recebe uma lista de tenants, e nenhuma aceita não
receber `tenant_id`.

Duas decisões que valem explicação:

**Recuperação por texto, não por vetor.** A Anthropic não tem API de
embeddings: busca vetorial exigiria um segundo fornecedor, uma chave a mais e
um custo por documento indexado. A busca textual do PostgreSQL é nativa, roda
na imagem oficial do banco e é testável sem rede. O que ela não faz é achar
sinônimo que não aparece no texto — a coluna `embedding` segue no modelo para
o dia em que isso justificar o fornecedor.

**Fatias por parágrafo, sem sobreposição.** Chunk que corta no meio da frase
estraga a resposta do agente mais do que perde na recuperação. Sobreposição
existe para busca vetorial, onde o trecho é comparado como um todo; na busca
por termo, o termo casa de um lado ou do outro.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from sqlalchemy import func, literal, select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.knowledge import DocumentStatus, KnowledgeChunk, KnowledgeDocument

#: Alvo de tamanho da fatia. Grande o bastante para uma ideia completa, pequeno
#: o bastante para caberem várias no contexto do modelo sem estourar o custo.
MAX_CHARS = 1200
#: Abaixo disso, a fatia é juntada à seguinte em vez de virar um trecho solto.
MIN_CHARS = 120
#: Teto por documento. Não é limite de plano (esse está em `services/limits`):
#: é o ponto em que um upload errado — um dump de banco, um log — pararia de
#: ser conhecimento e passaria a ser custo.
MAX_DOCUMENT_CHARS = 2_000_000

#: Configuração de stemming. Uma só, e em português: a maioria das bases é em
#: pt-BR, e texto em inglês continua sendo indexado, só sem reduzir plural.
FTS_CONFIG = "portuguese"


class EmptyDocument(AppError):
    code = "empty_document"

    def __init__(self) -> None:
        super().__init__(
            "O documento chegou sem texto. Formatos aceitos: texto, Markdown e CSV — "
            "PDF e Word precisam ser convertidos para texto antes do envio."
        )


class DocumentTooLarge(AppError):
    code = "document_too_large"

    def __init__(self, tamanho: int) -> None:
        super().__init__(
            f"O documento tem {tamanho:,} caracteres, acima do limite de "
            f"{MAX_DOCUMENT_CHARS:,}. Divida em partes menores."
        )


# ------------------------------------------------------------------ fatiamento
def _paragrafos(texto: str) -> list[str]:
    limpo = texto.replace("\r\n", "\n").replace("\r", "\n")
    return [p.strip() for p in re.split(r"\n\s*\n", limpo) if p.strip()]


def _quebrar_longo(paragrafo: str) -> list[str]:
    """Parágrafo maior que a fatia: quebra por frase, e só então na força.

    O corte na força existe porque texto colado de PDF às vezes vem sem
    pontuação nenhuma — e nesse caso perder o meio da frase é melhor do que
    mandar duzentos mil caracteres para o modelo.
    """
    frases = re.split(r"(?<=[.!?])\s+", paragrafo)
    fatias: list[str] = []
    atual = ""
    for frase in frases:
        while len(frase) > MAX_CHARS:
            if atual:
                fatias.append(atual.strip())
                atual = ""
            fatias.append(frase[:MAX_CHARS].strip())
            frase = frase[MAX_CHARS:]
        if len(atual) + len(frase) + 1 > MAX_CHARS and atual:
            fatias.append(atual.strip())
            atual = frase
        else:
            atual = f"{atual} {frase}".strip()
    if atual.strip():
        fatias.append(atual.strip())
    return fatias


def chunk_text(texto: str) -> list[str]:
    """Fatia o texto respeitando parágrafo, depois frase, depois o limite."""
    fatias: list[str] = []
    atual = ""
    for paragrafo in _paragrafos(texto):
        if len(paragrafo) > MAX_CHARS:
            if atual:
                fatias.append(atual)
                atual = ""
            fatias.extend(_quebrar_longo(paragrafo))
            continue
        candidato = f"{atual}\n\n{paragrafo}".strip() if atual else paragrafo
        if len(candidato) > MAX_CHARS:
            fatias.append(atual)
            atual = paragrafo
        else:
            atual = candidato
    if atual:
        fatias.append(atual)

    # Uma sobra curta no fim vira ruído na busca; junta com a anterior.
    if len(fatias) > 1 and len(fatias[-1]) < MIN_CHARS:
        cauda = fatias.pop()
        if len(fatias[-1]) + len(cauda) + 2 <= MAX_CHARS * 2:
            fatias[-1] = f"{fatias[-1]}\n\n{cauda}"
        else:
            fatias.append(cauda)
    return fatias


def estimar_tokens(texto: str) -> int:
    """Estimativa, não medição: quatro caracteres por token é a regra de bolso.

    Serve para a tela dar ordem de grandeza do que vai no contexto. Medir de
    verdade exigiria uma chamada de API por documento.
    """
    return max(1, len(texto) // 4)


# -------------------------------------------------------------------- ingestão
def ingest_document(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    title: str,
    content: str,
    source_type: str = "upload",
    source_uri: str | None = None,
    mime_type: str | None = None,
    campaign_ids: list[str] | None = None,
    uploaded_by: uuid.UUID | None = None,
) -> tuple[KnowledgeDocument, int]:
    """Cria o documento e suas fatias. Devolve (documento, número de fatias).

    Reenviar o mesmo conteúdo não duplica: o checksum é a chave. Quem carrega
    um playbook duas vezes não deveria ver o agente citar o mesmo trecho em
    dobro — e ver isso acontecer é o tipo de coisa que faz a empresa desconfiar
    de tudo o que o agente responde.
    """
    texto = (content or "").strip()
    if not texto:
        raise EmptyDocument()
    if len(texto) > MAX_DOCUMENT_CHARS:
        raise DocumentTooLarge(len(texto))

    checksum = hashlib.sha256(texto.encode()).hexdigest()
    existente = session.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == tenant_id)
        .where(KnowledgeDocument.checksum == checksum)
    ).scalar_one_or_none()
    if existente is not None:
        total = session.execute(
            select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.document_id == existente.id)
        ).scalar_one()
        return existente, int(total)

    documento = KnowledgeDocument(
        tenant_id=tenant_id,
        title=title.strip() or "Sem título",
        source_type=source_type,
        source_uri=source_uri,
        mime_type=mime_type,
        status=DocumentStatus.PROCESSING.value,
        campaign_ids=campaign_ids or [],
        checksum=checksum,
        char_count=len(texto),
        uploaded_by=uploaded_by,
    )
    session.add(documento)
    session.flush()

    fatias = chunk_text(texto)
    for ordinal, fatia in enumerate(fatias):
        session.add(
            KnowledgeChunk(
                tenant_id=tenant_id,
                document_id=documento.id,
                ordinal=ordinal,
                content=fatia,
                token_count=estimar_tokens(fatia),
            )
        )
    # O status só vira `indexed` depois das fatias existirem: um documento
    # marcado como indexado sem fatia faria o agente dizer que a base está
    # vazia sobre um arquivo que a tela mostra como pronto.
    documento.status = DocumentStatus.INDEXED.value
    session.flush()
    return documento, len(fatias)


# ----------------------------------------------------------------- recuperação
#: A busca é escrita em SQL direto porque é inerentemente do PostgreSQL —
#: `tsquery`, `ts_rank`, `ts_headline` — e montar isso pelo construtor de
#: expressões só esconderia o que está acontecendo.
#:
#: O `replace(... '&', '|')` é o detalhe que faz a diferença entre funcionar e
#: não funcionar. O `websearch_to_tsquery` liga os termos com E: a pergunta de
#: um lead ("Como funciona o reembolso se eu cancelar?") exigiria que o trecho
#: contivesse "funciona", "reembolso" E "cancelar" — e aí nada casa, a busca
#: recua para os mais recentes e o agente responde que não sabe. Trocando por
#: OU, qualquer termo casa e o `ts_rank` decide quem responde melhor. A
#: conversão pelo texto do próprio `tsquery` reaproveita a análise do banco,
#: inclusive o descarte de palavras vazias e as frases entre aspas, que usam
#: `<->` e passam intactas.
#:
#: O `CAST(:cfg AS regconfig)` em vez de `:cfg::regconfig` não é estilo: o
#: `text()` do SQLAlchemy lê `:cfg::` como nome de parâmetro e o SQL não chega
#: a ser válido.
SQL_BUSCA = """
WITH pergunta AS (
    SELECT replace(
        websearch_to_tsquery(CAST(:cfg AS regconfig), :termos)::text, '&', '|'
    )::tsquery AS tsq
)
SELECT c.id AS chunk_id,
       c.document_id,
       d.title,
       c.ordinal,
       c.content,
       ts_rank(c.search_vector, pergunta.tsq)
         + 0.5 * ts_rank(
             to_tsvector(CAST(:cfg AS regconfig), coalesce(d.title, '')), pergunta.tsq
           ) AS relevancia,
       ts_headline(
           CAST(:cfg AS regconfig), c.content, pergunta.tsq,
           'MaxWords=45, MinWords=20, ShortWord=3, MaxFragments=1'
       ) AS destaque
FROM knowledge_chunks c
JOIN knowledge_documents d ON d.id = c.document_id
CROSS JOIN pergunta
WHERE c.tenant_id = :tenant_id
  AND d.tenant_id = :tenant_id
  AND d.status = :indexado
  AND (
        jsonb_array_length(d.campaign_ids) = 0
        OR (
            CAST(:campaign_id AS text) IS NOT NULL
            AND d.campaign_ids ? CAST(:campaign_id AS text)
        )
      )
  AND c.search_vector @@ pergunta.tsq
ORDER BY relevancia DESC, c.ordinal
LIMIT :limite
"""


def _escopo_de_campanha(campaign_id: uuid.UUID | None):
    """Documento sem `campaign_ids` vale para a empresa inteira; com lista, só
    para as campanhas listadas."""
    sem_escopo = func.jsonb_array_length(KnowledgeDocument.campaign_ids) == 0
    if campaign_id is None:
        return sem_escopo
    contem = KnowledgeDocument.campaign_ids.op("?")(literal(str(campaign_id)))
    return sem_escopo | contem


def search_chunks(
    session: Session,
    tenant_id: uuid.UUID,
    query: str | None,
    *,
    campaign_id: uuid.UUID | None = None,
    limit: int = 20,
) -> list[dict]:
    """Os trechos mais relevantes para a pergunta — não os mais recentes.

    Sem pergunta, ou quando nenhum termo dela aparece na base, cai para os mais
    recentes: devolver nada faria o agente escalar para humano por falta de
    contexto, o que é pior do que devolver contexto imperfeito.
    """
    termos = (query or "").strip()
    if termos:
        linhas = (
            session.execute(
                text(SQL_BUSCA),
                {
                    "cfg": FTS_CONFIG,
                    "termos": termos,
                    "tenant_id": tenant_id,
                    "indexado": DocumentStatus.INDEXED.value,
                    "campaign_id": str(campaign_id) if campaign_id else None,
                    "limite": limit,
                },
            )
            .mappings()
            .all()
        )
        if linhas:
            return [
                {
                    "chunk_id": str(linha["chunk_id"]),
                    "document_id": str(linha["document_id"]),
                    "title": linha["title"],
                    "ordinal": linha["ordinal"],
                    "content": linha["content"],
                    "relevance": round(float(linha["relevancia"]), 6),
                    "excerpt": linha["destaque"],
                }
                for linha in linhas
            ]

    recentes = (
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(KnowledgeChunk.tenant_id == tenant_id)
        .where(KnowledgeDocument.tenant_id == tenant_id)
        .where(KnowledgeDocument.status == DocumentStatus.INDEXED.value)
        .where(_escopo_de_campanha(campaign_id))
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeChunk.ordinal)
        .limit(limit)
    )
    return [
        _como_dict(chunk, documento, relevancia=0.0, destaque=None)
        for chunk, documento in session.execute(recentes).all()
    ]


def _como_dict(
    chunk: KnowledgeChunk,
    documento: KnowledgeDocument,
    *,
    relevancia: float,
    destaque: str | None,
) -> dict:
    return {
        "chunk_id": str(chunk.id),
        "document_id": str(documento.id),
        "title": documento.title,
        "ordinal": chunk.ordinal,
        "content": chunk.content,
        "relevance": round(relevancia, 6),
        "excerpt": destaque,
    }


def counts_by_document(session: Session, tenant_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = session.execute(
        select(KnowledgeChunk.document_id, func.count(KnowledgeChunk.id))
        .where(KnowledgeChunk.tenant_id == tenant_id)
        .group_by(KnowledgeChunk.document_id)
    ).all()
    return {doc_id: int(total) for doc_id, total in rows}
