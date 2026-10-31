"""O pré-voo do go-live: o que ele impede, o que ele só avisa, e o que ele não afirma.

Um pré-voo tem duas formas de ser inútil. A primeira é reclamar de tudo: quem
recebe vinte linhas vermelhas para uma instalação que funciona aprende a ignorar
o comando. A segunda, pior, é dizer ✓ para o que não verificou — e é por isso que
este arquivo também testa a lista do que fica de fora.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models.platform import Tenant, User
from app.db.session import tenant_session, unscoped_session
from app.services import ai_credentials, email_accounts, retencao
from scripts.pronto_para_ir_ao_ar import main as preflight


def _promover(email: str) -> None:
    with unscoped_session(reason="test:preflight") as session:
        session.execute(
            select(User).where(User.email == email)
        ).scalar_one().is_platform_admin = True


def _configurar_ia(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        ai_credentials.store(session, tenant_id, api_key="sk-ant-de-teste", created_by=None)


def _configurar_email(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        integracao = email_accounts.store(
            session,
            tenant_id,
            provider="smtp",
            from_email="vendas@empresa.com",
            from_name="Vendas",
            username="vendas@empresa.com",
            password="senha",
            host="smtp.empresa.com",
            port=587,
            imap_host="imap.empresa.com",
            imap_port=993,
        )
        # O `store` não testa a conexão (a tela testa antes de salvar); aqui o que
        # importa é o estado que o pré-voo lê.
        integracao.status = "connected"


def test_empresa_sem_chave_e_sem_email_impede_o_go_live(make_tenant, capsys):
    t = make_tenant()
    _promover(t["email"])

    assert preflight([]) == 1
    saida = capsys.readouterr().out
    assert "sem chave da Anthropic" in saida
    assert "sem conta de email" in saida


def test_com_chave_e_email_nada_impede(make_tenant, capsys):
    t = make_tenant()
    _promover(t["email"])
    _configurar_ia(t["tenant_id"])
    _configurar_email(t["tenant_id"])

    assert preflight([]) == 0
    saida = capsys.readouterr().out
    assert "nada impede a operação" in saida or "pronto para operar" in saida


def test_sem_platform_admin_impede(make_tenant, capsys):
    """Sem a marca, o painel, o fechamento do mês e o suporte ficam inacessíveis.

    E ela é concedida por linha de comando, no servidor: não é algo que alguém
    descubra sozinho depois, olhando a tela.
    """
    t = make_tenant()
    _configurar_ia(t["tenant_id"])
    _configurar_email(t["tenant_id"])

    assert preflight([]) == 1
    saida = capsys.readouterr().out
    assert "platform admin" in saida
    assert "promover_admin" in saida, "a recusa precisa dizer o comando que resolve"


def test_empresa_desativada_nao_e_cobrada_no_preflight(make_tenant, capsys):
    """Empresa suspensa não precisa de chave: ninguém dela entra."""
    ativa, suspensa = make_tenant(), make_tenant()
    _promover(ativa["email"])
    _configurar_ia(ativa["tenant_id"])
    _configurar_email(ativa["tenant_id"])
    with unscoped_session(reason="test:preflight") as session:
        session.get(Tenant, suspensa["tenant_id"]).is_active = False

    assert preflight([]) == 0
    assert suspensa["slug"] not in capsys.readouterr().out


def test_o_aviso_de_descarte_nao_impede(make_tenant, capsys):
    """Sem prazo de retenção é o padrão e uma escolha — aviso, não impedimento."""
    t = make_tenant()
    _promover(t["email"])
    _configurar_ia(t["tenant_id"])
    _configurar_email(t["tenant_id"])

    assert preflight([]) == 0
    assert "sem prazo de descarte" in capsys.readouterr().out


def test_prazo_configurado_vira_confirmacao(make_tenant, capsys):
    t = make_tenant()
    _promover(t["email"])
    _configurar_ia(t["tenant_id"])
    _configurar_email(t["tenant_id"])
    with unscoped_session(reason="test:preflight") as session:
        retencao.salvar(
            session.get(Tenant, t["tenant_id"]), retencao.Politica(dias=180, leads_frios=False)
        )

    assert preflight([]) == 0
    assert "descarte automático em 180 dias" in capsys.readouterr().out


def test_o_preflight_diz_o_que_nao_verifica(make_tenant, capsys):
    """A parte mais importante: não afirmar o que não foi verificado.

    Backup restaurável, DNS propagado, SPF/DKIM/DMARC e a qualidade do texto dos
    agentes não cabem num comando. Listá-los como pendência humana é honesto;
    marcá-los ✓ seria mentira com aparência de rigor.
    """
    t = make_tenant()
    _promover(t["email"])
    _configurar_ia(t["tenant_id"])
    _configurar_email(t["tenant_id"])

    preflight([])
    saida = capsys.readouterr().out
    assert "não verifica" in saida
    for tema in ("backup", "DNS", "SPF", "primeiro disparo"):
        assert tema in saida, f"o pré-voo precisa lembrar de {tema}"


def test_email_conectado_com_erro_impede(make_tenant, capsys):
    """Conta salva e quebrada é pior do que conta ausente: parece configurada."""
    t = make_tenant()
    _promover(t["email"])
    _configurar_ia(t["tenant_id"])
    _configurar_email(t["tenant_id"])
    with tenant_session(t["tenant_id"]) as session:
        from app.db.models.ai import Integration

        integracao = session.execute(
            select(Integration).where(Integration.provider == "smtp")
        ).scalar_one()
        integracao.status = "error"

    assert preflight([]) == 1
    assert "status 'error'" in capsys.readouterr().out
