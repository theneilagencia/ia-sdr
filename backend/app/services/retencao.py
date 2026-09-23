"""Retenção: o que a plataforma descarta sozinha, e o que ela nunca descarta.

Guardar tudo para sempre é uma decisão, e é a errada: dado pessoal de quem
nunca respondeu um email não fica mais útil com o tempo, fica mais perigoso. A
LGPD chama isso de necessidade — dado guardado sem finalidade é dado a menos que
se devia ter. Do lado prático, um banco que só cresce fica mais lento e mais caro
de guardar e de restaurar.

Três princípios, na ordem em que decidem:

1. **A plataforma não escolhe pelo cliente.** Nasce em zero: nada é descartado
   até alguém definir o prazo. Um padrão de "90 dias" apagaria histórico de quem
   nunca pediu isso, e apagar não tem desfazer.
2. **Descadastro sobrevive a tudo.** O contato que pediu para não receber mais
   email nunca é apagado, em nenhum prazo. Apagar o registro do pedido faria a
   plataforma escrever para essa pessoa de novo no próximo import — e honrar o
   pedido é obrigação legal, não cortesia.
3. **Não se apaga o que ainda não foi cobrado nem auditado.** Consumo de um mês
   sem fatura emitida é a base de uma cobrança que ainda vai acontecer. E o log
   de auditoria tem piso próprio: é o registro de quem fez o quê, e um prazo
   curto de retenção não pode apagar a prova de um incidente.

O que **não** entra nesta versão, de propósito: base de conhecimento,
Company Brain, campanhas, faturas, contas e vínculos. São o acervo do cliente,
não rastro operacional — e apagar acervo por prazo seria a plataforma decidindo
o que a empresa precisa ter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.models.ai import AgentRun, RunStatus
from app.db.models.engagement import (
    Conversation,
    Meeting,
    Message,
    MessageDirection,
    MessageStatus,
    Qualification,
)
from app.db.models.jobs import Job, JobStatus
from app.db.models.platform import AuditLog, Invoice, InvoiceStatus, Tenant, UsageEvent
from app.db.models.sales import Contact, Prospect, ProspectStatus

#: Chave dentro de `tenants.settings`. Mesmo padrão da política de envio: o que é
#: configuração de operação mora no JSONB, o que é contrato mora em coluna.
CHAVE = "retention"

#: O log de auditoria não desce disto, qualquer que seja o prazo escolhido. É o
#: registro de quem fez o quê — quem convidou, quem aprovou, quem exportou — e
#: seis meses depois de um incidente é quando alguém costuma precisar dele.
PISO_AUDITORIA_DIAS = 90

#: Prospect que chegou até aqui teve resposta, reunião ou veredito: não é lead
#: frio, é histórico comercial da empresa. Fica fora do descarte automático
#: mesmo com "incluir leads que nunca responderam" ligado.
ESTADOS_COM_HISTORICO = frozenset(
    {
        ProspectStatus.ENGAGED.value,
        ProspectStatus.QUALIFIED.value,
        ProspectStatus.MEETING_BOOKED.value,
        ProspectStatus.DISQUALIFIED.value,
    }
)

#: Mensagem nesses estados é trabalho pendente, não histórico: rascunho à espera
#: de revisão e mensagem aprovada à espera de envio. Apagá-las por prazo seria
#: jogar fora trabalho que alguém ainda vai olhar.
ESTADOS_PENDENTES_DE_MENSAGEM = frozenset(
    {MessageStatus.DRAFT.value, MessageStatus.QUEUED.value}
)


@dataclass(frozen=True, slots=True)
class Politica:
    """O prazo, e se leads frios entram nele."""

    dias: int = 0
    leads_frios: bool = False

    @property
    def ligada(self) -> bool:
        return self.dias > 0

    def corte(self, agora: datetime | None = None) -> datetime:
        return (agora or datetime.now(UTC)) - timedelta(days=self.dias)

    def corte_auditoria(self, agora: datetime | None = None) -> datetime:
        """O piso vence o prazo: 30 dias de retenção não apaga auditoria de 60."""
        dias = max(self.dias, PISO_AUDITORIA_DIAS)
        return (agora or datetime.now(UTC)) - timedelta(days=dias)


def politica(tenant: Tenant) -> Politica:
    bruto = (tenant.settings or {}).get(CHAVE) or {}
    return Politica(
        dias=int(bruto.get("days") or 0),
        leads_frios=bool(bruto.get("include_cold_prospects") or False),
    )


def salvar(tenant: Tenant, nova: Politica) -> None:
    tenant.settings = {
        **(tenant.settings or {}),
        CHAVE: {"days": nova.dias, "include_cold_prospects": nova.leads_frios},
    }


def _periodos_faturados(session: Session, tenant_id) -> list[tuple[int, int]]:
    """Meses cuja fatura já foi emitida ou paga.

    Consumo de mês sem fatura emitida é a base de uma cobrança que ainda vai
    acontecer: apagá-lo seria faturar por estimativa depois.
    """
    linhas = session.execute(
        select(Invoice.period_year, Invoice.period_month)
        .where(Invoice.tenant_id == tenant_id)
        .where(Invoice.status.in_([InvoiceStatus.ISSUED.value, InvoiceStatus.PAID.value]))
    ).all()
    return [(int(a), int(m)) for a, m in linhas]


def _condicao_de_consumo(session: Session, tenant_id, corte: datetime):
    """Consumo velho **e** de um mês já faturado. Sem fatura, não sai."""
    periodos = _periodos_faturados(session, tenant_id)
    if not periodos:
        return None
    dentro_de_periodo_faturado = or_(
        *[
            and_(
                func.extract("year", UsageEvent.created_at) == ano,
                func.extract("month", UsageEvent.created_at) == mes,
            )
            for ano, mes in periodos
        ]
    )
    return and_(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.created_at < corte,
        dentro_de_periodo_faturado,
    )


def _prospects_frios(tenant_id, corte: datetime):
    """Lead que nunca reagiu e parou de andar antes do corte.

    Três guardas: estado sem histórico comercial, nenhuma reunião marcada e
    nenhum veredito de qualificação. Qualquer um desses três é sinal de que
    alguém trabalhou aquele lead, e trabalho de vendas não é rastro operacional.
    """
    # Subconsultas explícitas, e não `exists().where(...)` encadeado: com o
    # encadeamento, o `exists` de dentro decide sozinho o próprio FROM e a
    # correlação com o prospect se perde — a condição passa a ser "existe
    # **alguma** resposta em **alguma** conversa", que é verdadeira para
    # praticamente qualquer empresa e faria o filtro não filtrar nada. O caminho
    # de um dado que some por engano é longo demais para ficar implícito.
    sem_reuniao = ~select(Meeting.id).where(Meeting.prospect_id == Prospect.id).exists()
    sem_veredito = (
        ~select(Qualification.id).where(Qualification.prospect_id == Prospect.id).exists()
    )
    # Uma mensagem de entrada é a pessoa falando com a empresa: deixa de ser lead
    # frio na hora, qualquer que seja o estado em que o funil o deixou.
    sem_resposta = (
        ~select(Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.prospect_id == Prospect.id)
        .where(Message.direction == MessageDirection.INBOUND.value)
        .exists()
    )
    return and_(
        Prospect.tenant_id == tenant_id,
        Prospect.status.notin_(list(ESTADOS_COM_HISTORICO)),
        func.coalesce(Prospect.last_activity_at, Prospect.created_at) < corte,
        sem_reuniao,
        sem_veredito,
        sem_resposta,
    )


def previsao(session: Session, tenant_id, *, agora: datetime | None = None) -> dict[str, int]:
    """Quanto sairia hoje, por classe, sem apagar nada.

    Existe para a tela poder mostrar o número **antes** de a pessoa ligar o
    prazo. Apagar não tem desfazer, e "5.312 mensagens" na tela é o que faz
    alguém reler o prazo antes de salvar.
    """
    return _rodar(session, tenant_id, agora=agora, apagar=False)


def aplicar(session: Session, tenant_id, *, agora: datetime | None = None) -> dict[str, int]:
    """Descarta o que passou do prazo. Devolve o que saiu, por classe."""
    return _rodar(session, tenant_id, agora=agora, apagar=True)


def _rodar(session: Session, tenant_id, *, agora: datetime | None, apagar: bool) -> dict[str, int]:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return {}
    pol = politica(tenant)
    if not pol.ligada:
        return {}

    corte = pol.corte(agora)
    saida: dict[str, int] = {}

    # Quem é lead frio se decide **antes** de qualquer delete, e os ids ficam
    # guardados. Não é preciosismo de ordem: a prova de que um lead respondeu é
    # uma mensagem de entrada, e a regra de mensagens apaga mensagem velha. Na
    # primeira versão deste arquivo as mensagens saíam primeiro, e um lead que
    # respondeu há dois anos passava a parecer que nunca respondeu — a mesma
    # rodada apagava a resposta e, em seguida, o lead. O teste
    # `test_lead_que_respondeu_nao_e_frio` existe por causa disso.
    ids_frios: list = []
    if pol.leads_frios:
        ids_frios = list(
            session.execute(select(Prospect.id).where(_prospects_frios(tenant_id, corte)))
            .scalars()
            .all()
        )

    def contar(modelo, condicao) -> int:
        return int(
            session.execute(
                select(func.count()).select_from(modelo).where(condicao)
            ).scalar_one()
        )

    def processar(nome: str, modelo, condicao) -> None:
        quantos = contar(modelo, condicao)
        if quantos and apagar:
            session.execute(delete(modelo).where(condicao))
        saida[nome] = quantos

    processar(
        "jobs",
        Job,
        and_(
            Job.tenant_id == tenant_id,
            Job.status.in_([JobStatus.DONE.value, JobStatus.FAILED.value]),
            Job.created_at < corte,
        ),
    )
    processar(
        "agent_runs",
        AgentRun,
        and_(
            AgentRun.tenant_id == tenant_id,
            AgentRun.status.in_(
                [RunStatus.SUCCEEDED.value, RunStatus.FAILED.value, RunStatus.REJECTED.value]
            ),
            AgentRun.created_at < corte,
        ),
    )
    processar(
        "messages",
        Message,
        and_(
            Message.tenant_id == tenant_id,
            Message.status.notin_(list(ESTADOS_PENDENTES_DE_MENSAGEM)),
            Message.created_at < corte,
        ),
    )
    processar(
        "audit_logs",
        AuditLog,
        and_(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at < pol.corte_auditoria(agora),
        ),
    )

    consumo = _condicao_de_consumo(session, tenant_id, corte)
    if consumo is None:
        saida["usage_events"] = 0
    else:
        processar("usage_events", UsageEvent, consumo)

    if pol.leads_frios:
        alvo = and_(Prospect.tenant_id == tenant_id, Prospect.id.in_(ids_frios or [None]))
        processar("prospects", Prospect, alvo)
        # O contato sai depois do prospect e só se não sobrar nenhum outro.
        # `opted_out` nunca sai: o registro do descadastro precisa sobreviver a
        # qualquer prazo, senão a plataforma volta a escrever para quem pediu
        # para não receber mais — no próximo import da mesma lista.
        #
        # Na previsão os prospects ainda estão lá, então "ficaria órfão" é
        # calculado ignorando justamente os que vão sair; contar "contato sem
        # prospect" antes do delete devolveria zero, a tela mostraria zero e a
        # execução apagaria dezenas. Previsão que mente é pior do que nenhuma.
        if apagar:
            sobrou_prospect = select(Prospect.id).where(Prospect.contact_id == Contact.id).exists()
        else:
            sobrou_prospect = (
                select(Prospect.id)
                .where(Prospect.contact_id == Contact.id)
                .where(Prospect.id.notin_(ids_frios or [None]))
                .exists()
            )
        orfaos = and_(
            Contact.tenant_id == tenant_id,
            Contact.opted_out.is_(False),
            Contact.created_at < corte,
            ~sobrou_prospect,
        )
        processar("contacts", Contact, orfaos)
    else:
        saida["prospects"] = 0
        saida["contacts"] = 0

    if apagar:
        session.flush()
    return saida


__all__ = [
    "CHAVE",
    "PISO_AUDITORIA_DIAS",
    "Politica",
    "aplicar",
    "politica",
    "previsao",
    "salvar",
]
