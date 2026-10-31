"""Rotas públicas, sem autenticação.

Só existe uma, e ela precisa ser pública: o descadastro. Quem recebeu o email
não tem conta na plataforma — exigir login para sair da lista é o mesmo que
não oferecer saída.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.db.models.sales import Contact, Prospect, ProspectStatus
from app.db.session import unscoped_session
from app.services.unsubscribe import parse_token

router = APIRouter(prefix="/public", tags=["public"])

PAGINA = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Descadastro</title>
<style>
body{{font:16px/1.6 ui-sans-serif,system-ui,sans-serif;background:#fbfaf9;color:#1c1b19;
display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}}
main{{max-width:420px;background:#fff;border:1px solid #e6e2dd;border-radius:12px;padding:32px}}
h1{{font-size:20px;margin:0 0 8px}} p{{color:#6b6560;margin:0}}
@media (prefers-color-scheme: dark){{body{{background:#17161a;color:#f2efec}}
main{{background:#1f1e23;border-color:#322f38}} p{{color:#a39d98}}}}
</style></head>
<body><main><h1>{titulo}</h1><p>{texto}</p></main></body></html>"""


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
@router.post("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str) -> HTMLResponse:
    """Descadastra em um clique, sem pedir nada em troca.

    Aceita GET e POST: o GET é o link do corpo do email, o POST é o botão
    nativo de "cancelar inscrição" do Gmail e do Outlook.

    Atravessa o RLS de propósito e por um motivo específico: não há sessão nem
    tenant ativo aqui — quem clica é o destinatário, não um usuário. O token
    assinado é o que identifica tenant e contato, e nada além do descadastro
    daquele contato acontece.
    """
    try:
        tenant_id, contact_id = parse_token(token)
    except Exception:
        return HTMLResponse(
            PAGINA.format(
                titulo="Link inválido",
                texto="Este link de descadastro não é válido. Responda ao email pedindo "
                "a remoção e faremos manualmente.",
            ),
            status_code=400,
        )

    with unscoped_session(reason="public:unsubscribe") as session:
        contato = session.execute(
            select(Contact).where(Contact.id == contact_id).where(Contact.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if contato is None:
            # Mesma resposta de sucesso: dizer "não achei" confirmaria para um
            # curioso que aquele contato não está na base.
            return HTMLResponse(
                PAGINA.format(titulo="Pronto", texto="Você não receberá mais mensagens.")
            )

        contato.opted_out = True
        for prospect in session.execute(
            select(Prospect).where(Prospect.contact_id == contato.id)
        ).scalars():
            prospect.status = ProspectStatus.DISQUALIFIED.value

    return HTMLResponse(
        PAGINA.format(
            titulo="Pronto",
            texto="Você não receberá mais mensagens. Desculpe pelo incômodo.",
        )
    )
