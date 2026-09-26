"""Integração com o RAVI — o CRM que já existe.

Esta plataforma não tem CRM e não vai ter. O lead vive no RAVI; o que o AI SDR
faz é pesquisar, pontuar, abordar e qualificar, e empurrar isso para lá. Duas
bases com o mesmo lead divergem em uma semana, e a partir dali ninguém sabe
qual está certa.

**O contrato.** `POST /leads` do serviço de chat do RAVI, autenticado por
`Authorization: Bearer <token>` e `x-tenant-id`. O corpo é BANT — `company`,
`role`, `intent`, `budget`, `authority`, `need`, `timeline`, `score` de 0 a 100,
`source` e `stage` — que é quase exatamente o que o Qualification Agent produz.
Do lado do RAVI o endpoint faz **upsert por email ou telefone** e calcula a
temperatura a partir do score; reenviar o mesmo lead é seguro por desenho, o
que é a propriedade que torna esta integração retentável.

**O que não é enviado.** Prospect sem pontuação não vai: o `score` é
obrigatório no RAVI, e empurrar lead sem pesquisa nem nota enche o CRM de linha
que ninguém sabe de onde veio — que é o jeito mais rápido de a equipe comercial
parar de confiar no que chega.

**Ressalva de procedência.** Este contrato foi lido em `theneilagencia/ravi`,
que é público. A fonte da verdade é `ApyMine/ravi`, privado, que esta sessão não
conseguiu abrir. Se o privado divergir, o que quebra é o teste de conexão e o
envio — em voz alta, com o corpo da resposta do RAVI no erro, nunca em silêncio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import AppError, NotFound
from app.db.models.ai import Integration, IntegrationProvider
from app.db.models.engagement import Qualification
from app.db.models.sales import Company, Contact, Prospect, Score

#: Tempo de espera por requisição. Curto de propósito: isso roda dentro de um
#: job, e job que fica pendurado dez minutos num CRM fora do ar atrasa a fila
#: inteira.
TIMEOUT = 15.0

#: O RAVI aceita só estes valores, e nenhum deles significa "prospecção
#: outbound". `manual` é o menos errado — e é uma informação que vale pedir para
#: o RAVI: um `source` próprio separaria o lead que chegou pelo chat do lead que
#: este motor foi buscar.
SOURCE = "manual"

#: Onde o vínculo com o RAVI fica gravado. Vai no contato, não no prospect,
#: porque o RAVI deduplica por email/telefone — ou seja, por pessoa, não por
#: participação em campanha.
CHAVE_NO_CONTATO = "ravi"


class RaviUnavailable(AppError):
    code = "ravi_unavailable"
    status_code = 502


class RaviConfigInvalid(AppError):
    code = "ravi_config_invalid"


# ------------------------------------------------------------------ credencial
def _integration(session: Session, tenant_id: uuid.UUID) -> Integration | None:
    return session.execute(
        select(Integration)
        .where(Integration.tenant_id == tenant_id)
        .where(Integration.provider == IntegrationProvider.RAVI.value)
        .limit(1)
    ).scalar_one_or_none()


def _normalizar_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise RaviConfigInvalid("Informe a URL da API do RAVI.")
    if not url.startswith(("http://", "https://")):
        raise RaviConfigInvalid(f"A URL precisa começar com https:// — recebi '{base_url}'.")
    return url


def token_hint(token: str) -> str:
    """Só o fim do token, para a tela confirmar qual está salvo sem exibi-lo."""
    limpo = (token or "").strip()
    return f"…{limpo[-4:]}" if len(limpo) >= 4 else "…"


def describe(session: Session, tenant_id: uuid.UUID) -> dict:
    integracao = _integration(session, tenant_id)
    if integracao is None:
        return {
            "configured": False,
            "base_url": None,
            "ravi_tenant_id": None,
            "token_hint": None,
            "status": "missing",
            "last_error": None,
        }
    config = integracao.config or {}
    return {
        "configured": True,
        "base_url": config.get("base_url"),
        "ravi_tenant_id": integracao.account_ref,
        # A dica, nunca o token. Ele não tem campo de leitura em nenhuma
        # resposta desta API.
        "token_hint": config.get("token_hint"),
        "status": integracao.status,
        "last_error": integracao.last_error,
    }


def store(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    base_url: str,
    token: str,
    ravi_tenant_id: str,
    default_stage: str | None = None,
    created_by: uuid.UUID | None = None,
) -> Integration:
    url = _normalizar_url(base_url)
    if not token.strip():
        raise RaviConfigInvalid("Informe o token de agente do RAVI.")
    if not ravi_tenant_id.strip():
        raise RaviConfigInvalid(
            "Informe o identificador da empresa no RAVI — é o que vai no "
            "cabeçalho x-tenant-id, e sem ele o RAVI não sabe de quem é o lead."
        )

    integracao = _integration(session, tenant_id)
    if integracao is None:
        integracao = Integration(
            tenant_id=tenant_id,
            provider=IntegrationProvider.RAVI.value,
            created_by=created_by,
            credentials_encrypted="",
        )
        session.add(integracao)
    integracao.account_ref = ravi_tenant_id.strip()
    integracao.display_name = "RAVI"
    integracao.credentials_encrypted = encrypt_json({"token": token.strip()})
    integracao.config = {
        "base_url": url,
        "token_hint": token_hint(token),
        "default_stage": default_stage,
    }
    integracao.status = "connected"
    integracao.last_error = None
    session.flush()
    return integracao


def credentials(session: Session, tenant_id: uuid.UUID) -> dict:
    integracao = _integration(session, tenant_id)
    if integracao is None:
        raise NotFound("Esta empresa não tem o RAVI configurado")
    segredo = decrypt_json(integracao.credentials_encrypted)
    config = integracao.config or {}
    return {
        "base_url": config.get("base_url"),
        "token": segredo["token"],
        "ravi_tenant_id": integracao.account_ref,
        "default_stage": config.get("default_stage"),
    }


def remove(session: Session, tenant_id: uuid.UUID) -> None:
    integracao = _integration(session, tenant_id)
    if integracao is not None:
        session.delete(integracao)
        session.flush()


# ---------------------------------------------------------------------- cliente
def _cliente(base_url: str, token: str, ravi_tenant_id: str) -> httpx.Client:
    """A fábrica de cliente HTTP. É o único ponto que abre conexão para fora."""
    return httpx.Client(
        base_url=base_url,
        timeout=TIMEOUT,
        headers={
            "authorization": f"Bearer {token}",
            "x-tenant-id": ravi_tenant_id,
            "content-type": "application/json",
        },
    )


def _traduzir(resposta: httpx.Response) -> str:
    """A mensagem que a pessoa lê quando o RAVI recusa.

    O corpo da resposta entra no texto de propósito: se o contrato do RAVI
    mudar, é isso que vai dizer o que mudou — em vez de um 400 anônimo.
    """
    corpo = (resposta.text or "")[:300]
    if resposta.status_code in (401, 403):
        return (
            "O RAVI recusou o token (HTTP "
            f"{resposta.status_code}). Confira o token de agente e se ele "
            "pertence à empresa informada em x-tenant-id."
        )
    if resposta.status_code == 404:
        return (
            "A URL da API do RAVI respondeu 404. Confira o endereço — ele deve "
            f"apontar para o serviço que atende /leads. Resposta: {corpo}"
        )
    if resposta.status_code == 400:
        return f"O RAVI recusou os dados enviados: {corpo}"
    return f"O RAVI respondeu HTTP {resposta.status_code}: {corpo}"


def test_connection(
    *, base_url: str, token: str, ravi_tenant_id: str, client_factory=None
) -> tuple[bool, str]:
    """Bate no RAVI antes de salvar, com uma leitura que não escreve nada.

    Testar antes de salvar é o que transforma "não funcionou e não sei por quê"
    numa frase que a pessoa entende, enquanto ela ainda está olhando o campo.
    """
    try:
        url = _normalizar_url(base_url)
    except RaviConfigInvalid as erro:
        return False, str(erro)

    fabrica = client_factory or _cliente
    try:
        with fabrica(url, token, ravi_tenant_id) as cliente:
            resposta = cliente.get("/leads", params={"limit": 1})
    except httpx.TimeoutException:
        return False, f"O RAVI não respondeu em {int(TIMEOUT)} segundos."
    except (httpx.HTTPError, OSError) as erro:
        # O erro cru ("[Errno 111] Connection refused") não diz nada a quem está
        # preenchendo o campo, e o engano mais comum é um só: colar o endereço do
        # painel em vez do da API. A frase aponta isso e guarda o detalhe técnico
        # no fim, para quem for procurar no log.
        return False, (
            f"Não foi possível alcançar o RAVI em {url}. Confira se é o endereço da "
            f"API do RAVI, e não o do painel onde vocês entram. Detalhe: {erro}"
        )

    if resposta.status_code >= 400:
        return False, _traduzir(resposta)
    return True, "Conexão com o RAVI funcionando."


# --------------------------------------------------------------------- mapeamento
#: Palavras que identificam o critério da campanha como um dos campos BANT do
#: RAVI. O nome do critério é texto livre — quem monta a campanha escreve
#: "Orçamento aprovado" ou "Budget confirmado" —, então o casamento é por
#: palavra-chave, e o que não casa simplesmente não vira campo.
BANT = {
    "budget": ("orçament", "orcament", "budget", "verba", "investiment"),
    "authority": ("autoridad", "authority", "decisor", "decision", "sponsor"),
    "need": ("necessidad", "need", "dor", "pain", "problema"),
    "timeline": ("prazo", "timeline", "urgênc", "urgenc", "quando"),
}

#: Status legível, para quando o critério foi avaliado e não há frase de
#: evidência. `unknown` não entra: é ausência de informação, e mandar isso como
#: texto encheria a tela do RAVI de ruído que parece informação.
STATUS_LEGIVEL = {"met": "atendido", "not_met": "não atendido"}


def _campo_bant(nome: str) -> str | None:
    alvo = (nome or "").lower()
    for campo, palavras in BANT.items():
        if any(palavra in alvo for palavra in palavras):
            return campo
    return None


def _itens_de_criterio(resultados) -> list[dict]:
    """Normaliza o que está gravado em `criteria_results`.

    O Qualification Agent grava `{"criteria": [{criterion, status, evidence}]}`.
    A forma de dicionário chato — `{"orçamento": "..."}` — é aceita porque é o
    que dado escrito à mão ou importado de outro lugar tende a ter.
    """
    if not isinstance(resultados, dict):
        return []
    lista = resultados.get("criteria")
    if isinstance(lista, list):
        return [item for item in lista if isinstance(item, dict)]
    return [
        {"criterion": chave, "status": None, "evidence": valor}
        for chave, valor in resultados.items()
        if isinstance(valor, str)
    ] + [
        {"criterion": chave, **valor}
        for chave, valor in resultados.items()
        if isinstance(valor, dict)
    ]


def _criterios(qualificacao: Qualification | None) -> dict[str, str]:
    """Traduz os critérios da qualificação para os campos BANT do RAVI.

    O Qualification Agent avalia critério a critério; o RAVI tem campo próprio
    para orçamento, autoridade, necessidade e prazo. Critério sem evidência e
    com status desconhecido não vira campo — é justamente o caso que o agente
    produz quando rebaixa um "atendido" sem prova, e propagá-lo para o CRM
    transformaria a falta de informação em informação.
    """
    if qualificacao is None:
        return {}

    saida: dict[str, str] = {}
    for item in _itens_de_criterio(qualificacao.criteria_results):
        campo = _campo_bant(str(item.get("criterion") or ""))
        if campo is None or campo in saida:
            continue
        evidencia = str(item.get("evidence") or "").strip()
        status = str(item.get("status") or "").strip()
        texto = evidencia or STATUS_LEGIVEL.get(status, "")
        if not texto:
            continue
        if evidencia and status in STATUS_LEGIVEL:
            texto = f"{STATUS_LEGIVEL[status]} — {evidencia}"
        saida[campo] = texto[:500]
    return saida


def build_payload(session: Session, prospect: Prospect) -> dict:
    """O corpo do `POST /leads`. Devolve `{}` quando o prospect não deve ir.

    Sem pontuação não vai: o `score` é obrigatório no RAVI, e lead sem nota nem
    pesquisa é linha que ninguém sabe de onde veio.
    """
    contato = session.get(Contact, prospect.contact_id) if prospect.contact_id else None
    if contato is None:
        return {}
    if not (contato.email or contato.phone):
        # O upsert do RAVI é por email ou telefone. Sem nenhum dos dois, cada
        # envio criaria um lead novo — o oposto do que uma integração deve fazer.
        return {}

    nota = session.execute(
        select(Score)
        .where(Score.prospect_id == prospect.id)
        .order_by(Score.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if nota is None:
        return {}

    empresa = session.get(Company, prospect.company_id) if prospect.company_id else None
    qualificacao = session.execute(
        select(Qualification)
        .where(Qualification.prospect_id == prospect.id)
        .order_by(Qualification.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    payload: dict = {
        "name": contato.full_name or None,
        "email": contato.email or None,
        "phone": contato.phone or None,
        "company": empresa.name if empresa else None,
        "role": contato.title or None,
        # O RAVI calcula a temperatura a partir do score, então a nota do ICP é
        # o que decide se o lead chega quente do outro lado.
        "score": max(0, min(100, int(round(float(nota.value))))),
        "source": SOURCE,
        **_criterios(qualificacao),
    }
    if qualificacao is not None and qualificacao.rationale:
        payload["intent"] = qualificacao.rationale[:500]
    elif nota.rationale:
        payload["intent"] = nota.rationale[:500]

    return {chave: valor for chave, valor in payload.items() if valor is not None}


def _revisao(session: Session, prospect: Prospect) -> str:
    """Impressão do estado atual do prospect.

    É o que decide se há algo novo para enviar. Comparar por conteúdo evitaria
    envio à toa, mas o payload muda de forma com a qualificação; o instante da
    última mudança relevante é mais simples e não erra para o lado de não
    enviar.
    """
    marcos = [prospect.updated_at]
    for modelo in (Score, Qualification):
        ultimo = session.execute(
            select(modelo.created_at)
            .where(modelo.prospect_id == prospect.id)
            .order_by(modelo.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if ultimo is not None:
            marcos.append(ultimo)
    return max(m for m in marcos if m is not None).isoformat()


def _marca(contato: Contact) -> dict:
    return dict((contato.attributes or {}).get(CHAVE_NO_CONTATO) or {})


# -------------------------------------------------------------------- envio
def push_prospect(
    session: Session,
    tenant_id: uuid.UUID,
    prospect: Prospect,
    *,
    force: bool = False,
    client_factory=None,
    cfg: dict | None = None,
) -> dict:
    """Manda um prospect para o RAVI. Devolve o que aconteceu, para o log.

    Idempotente por desenho: o `POST /leads` do RAVI faz upsert por email ou
    telefone, então uma retentativa não cria lead repetido. É essa propriedade
    que permite este envio viver na fila com backoff em vez de precisar de
    controle de exatamente-uma-vez.
    """
    # A configuração é a primeira coisa verificada, e não a última: mandar a
    # pessoa buscar a nota do lead para depois dizer que o RAVI nunca foi
    # conectado é fazê-la trabalhar duas vezes na ordem errada. O envio em lote
    # já checava isto na entrada; aqui faltava.
    #
    # `cfg` entra pronto no caminho em lote: resolvê-lo por prospect seria uma
    # consulta e uma decifragem por linha do lote, e é o mesmo erro que uma
    # consulta por linha de tela.
    if cfg is None:
        cfg = credentials(session, tenant_id)

    payload = build_payload(session, prospect)
    if not payload:
        # A razão diz o que fazer, não só o que faltou: quem lê isto na tela
        # precisa saber qual é o próximo passo.
        return {
            "status": "skipped",
            "reason": (
                "o lead precisa de nota e de um email ou telefone. A nota sai da "
                "pesquisa; o endereço, do contato"
            ),
        }

    contato = session.get(Contact, prospect.contact_id)
    marca = _marca(contato)
    revisao = _revisao(session, prospect)
    if not force and marca.get("revision") == revisao:
        return {"status": "unchanged", "lead_id": marca.get("lead_id")}

    if cfg.get("default_stage"):
        payload["stage"] = cfg["default_stage"]

    fabrica = client_factory or _cliente
    try:
        with fabrica(cfg["base_url"], cfg["token"], cfg["ravi_tenant_id"]) as cliente:
            resposta = cliente.post("/leads", json=payload)
    except httpx.TimeoutException as erro:
        raise RaviUnavailable(f"O RAVI não respondeu em {int(TIMEOUT)} segundos.") from erro
    except (httpx.HTTPError, OSError) as erro:
        raise RaviUnavailable(f"Não foi possível alcançar o RAVI: {erro}") from erro

    if resposta.status_code >= 400:
        raise RaviUnavailable(_traduzir(resposta))

    lead = resposta.json() if resposta.content else {}
    lead_id = str(lead.get("id") or marca.get("lead_id") or "")
    contato.attributes = {
        **(contato.attributes or {}),
        CHAVE_NO_CONTATO: {
            "lead_id": lead_id or None,
            "revision": revisao,
            "synced_at": datetime.now(UTC).isoformat(),
            # 201 no RAVI significa lead novo; 200, lead que já existia lá.
            "created": resposta.status_code == 201,
        },
    }
    session.flush()
    return {
        "status": "created" if resposta.status_code == 201 else "updated",
        "lead_id": lead_id or None,
    }


def sync_pending(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int = 100,
    client_factory=None,
) -> dict:
    """Empurra para o RAVI tudo que mudou desde o último envio.

    É o que o worker chama. Sem o RAVI configurado, não faz nada e não reclama:
    a integração é opcional, e uma empresa que não usa o RAVI não deveria ver
    job falhando a cada ciclo.
    """
    if _integration(session, tenant_id) is None:
        return {"skipped": 0, "sent": 0, "unchanged": 0, "reason": "RAVI não configurado"}
    cfg = credentials(session, tenant_id)

    prospects = list(
        session.execute(
            select(Prospect).order_by(Prospect.updated_at.desc()).limit(limit)
        ).scalars()
    )

    resultado = {"sent": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    for prospect in prospects:
        try:
            saida = push_prospect(
                session, tenant_id, prospect, client_factory=client_factory, cfg=cfg
            )
        except RaviUnavailable:
            # Um lead recusado não pode impedir os outros de subir. A exceção
            # sobe para a fila só se nenhum passar — aqui ela é contada.
            resultado["failed"] += 1
            continue
        if saida["status"] in ("created", "updated"):
            resultado["sent"] += 1
        elif saida["status"] == "unchanged":
            resultado["unchanged"] += 1
        else:
            resultado["skipped"] += 1

    if resultado["sent"] == 0 and resultado["failed"] > 0:
        # Nada subiu e houve falha: isso é problema de configuração ou de
        # disponibilidade, e a fila deve tentar de novo com backoff.
        raise RaviUnavailable(f"Nenhum lead foi aceito pelo RAVI ({resultado['failed']} falha(s)).")
    return resultado
