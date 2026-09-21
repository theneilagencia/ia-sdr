"""Pontuação de aderência ao ICP.

Uma decisão que vale explicar: a nota **não** custa uma segunda chamada de
modelo. O Research Agent já julgou a aderência com evidência na mão; pagar de
novo para reinterpretar o próprio texto seria gastar duas vezes pela mesma
informação — e introduzir uma segunda opinião que pode discordar da primeira,
sem que ninguém saiba qual está certa.

O que este módulo faz é converter aquele julgamento em número, banda e
justificativa rastreável, com um ajuste por sinais de timing. Quando houver
dados de conversão suficientes para treinar algo melhor, o lugar de trocar a
regra é aqui, atrás da mesma interface.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.sales import Prospect, ProspectStatus, Research, Score

#: Aderência declarada pelo agente → nota base.
FIT_BASE = {"forte": 80.0, "parcial": 55.0, "fraco": 25.0, "desconhecido": 10.0}

#: Cada sinal de timing (contratação, expansão, rodada, troca de sistema) soma.
SIGNAL_BONUS = 4.0
MAX_SIGNAL_BONUS = 12.0

#: Muita coisa desconhecida derruba a confiança na nota.
UNKNOWN_PENALTY = 3.0
MAX_UNKNOWN_PENALTY = 15.0

#: Achado sem fonte vale menos do que achado com fonte.
EVIDENCE_BONUS = 2.0
MAX_EVIDENCE_BONUS = 8.0

BANDS = ((80.0, "A"), (60.0, "B"), (40.0, "C"), (0.0, "D"))


def band_for(value: float) -> str:
    for floor, band in BANDS:
        if value >= floor:
            return band
    return "D"


def score_research(findings: dict) -> tuple[float, dict]:
    """Traduz o julgamento do agente em nota, devolvendo o cálculo aberto.

    O dicionário de sinais é gravado junto com a nota: quem olhar depois vê
    por que deu 84 e não 62, sem precisar confiar na palavra do sistema.
    """
    fit = str(findings.get("icp_fit", "desconhecido"))
    base = FIT_BASE.get(fit, FIT_BASE["desconhecido"])

    sinais = findings.get("signals") or []
    unknowns = findings.get("unknowns") or []
    achados = findings.get("findings") or []
    com_fonte = [f for f in achados if isinstance(f, dict) and f.get("source_url")]

    bonus_sinais = min(len(sinais) * SIGNAL_BONUS, MAX_SIGNAL_BONUS)
    bonus_fonte = min(len(com_fonte) * EVIDENCE_BONUS, MAX_EVIDENCE_BONUS)
    penalidade = min(len(unknowns) * UNKNOWN_PENALTY, MAX_UNKNOWN_PENALTY)

    valor = max(0.0, min(100.0, base + bonus_sinais + bonus_fonte - penalidade))
    detalhe = {
        "icp_fit": fit,
        "base": base,
        "signals": len(sinais),
        "signal_bonus": bonus_sinais,
        "sourced_findings": len(com_fonte),
        "evidence_bonus": bonus_fonte,
        "unknowns": len(unknowns),
        "unknown_penalty": penalidade,
    }
    return round(valor, 2), detalhe


def apply_to_prospects(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    research: Research,
) -> list[Score]:
    """Pontua os prospects da conta pesquisada e move o funil.

    Uma pesquisa vale para todos os prospects daquela empresa na campanha —
    pesquisar a mesma conta uma vez por pessoa seria pagar várias vezes pelo
    mesmo trabalho.
    """
    stmt = select(Prospect).where(Prospect.tenant_id == tenant_id)
    if research.entity_type == "company":
        stmt = stmt.where(Prospect.company_id == research.entity_id)
    else:
        stmt = stmt.where(Prospect.contact_id == research.entity_id)
    if research.campaign_id:
        stmt = stmt.where(Prospect.campaign_id == research.campaign_id)

    valor, detalhe = score_research(research.findings or {})
    rationale = (research.findings or {}).get("icp_rationale")

    scores: list[Score] = []
    for prospect in session.execute(stmt).scalars():
        score = Score(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            campaign_id=prospect.campaign_id,
            value=valor,
            band=band_for(valor),
            rationale=rationale,
            signals=detalhe,
        )
        session.add(score)
        # Só avança quem ainda não passou deste ponto: quem já foi contatado
        # não volta para "scored" porque a conta foi pesquisada de novo.
        if prospect.status in (
            ProspectStatus.NEW.value,
            ProspectStatus.RESEARCHING.value,
            ProspectStatus.RESEARCHED.value,
        ):
            prospect.status = ProspectStatus.SCORED.value
        scores.append(score)

    session.flush()
    return scores
