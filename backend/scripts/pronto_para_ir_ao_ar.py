"""Pré-voo do go-live: o que falta para esta instalação operar de verdade.

    python -m scripts.pronto_para_ir_ao_ar

Existe porque "está publicado" e "está pronto para operar" são coisas
diferentes, e a distância entre as duas é uma lista de configuração que ninguém
decora: chave da Anthropic por empresa, conta de email conectada e testada,
campanha com critérios, segredo próprio para os links de descadastro, marca de
platform admin em alguém. Cada item, faltando, produz uma falha diferente e
todas no pior momento — na frente do primeiro cliente.

O comando separa o que impede de operar (✗) do que é escolha consciente (!). Sai
com erro só no primeiro caso: um pré-voo que reclama de tudo é um pré-voo que
ninguém roda duas vezes.

E diz o que **não** consegue verificar. Backup restaurável, DNS propagado,
certificado emitido e a qualidade do que a IA escreve não caberiam aqui como
✓ — afirmar o que não se verificou é pior do que não verificar.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.startup import InsecureConfiguration, verify_production_secrets
from app.db.models.platform import Tenant, User
from app.db.session import unscoped_session, verify_database_roles
from app.services import ai_credentials, email_accounts, retencao

VERDE = "\033[32m"
VERMELHO = "\033[31m"
AMARELO = "\033[33m"
FIM = "\033[0m"


class Relatorio:
    """Acumula o resultado e decide o código de saída."""

    def __init__(self) -> None:
        self.impedimentos = 0
        self.avisos = 0

    def ok(self, mensagem: str) -> None:
        print(f"{VERDE}✓{FIM} {mensagem}")

    def impede(self, mensagem: str, *, conserto: str) -> None:
        self.impedimentos += 1
        print(f"{VERMELHO}✗{FIM} {mensagem}")
        print(f"  → {conserto}")

    def avisa(self, mensagem: str) -> None:
        self.avisos += 1
        print(f"{AMARELO}!{FIM} {mensagem}")

    def titulo(self, texto: str) -> None:
        print(f"\n{texto}")


def _verificar_plataforma(rel: Relatorio) -> None:
    rel.titulo("Plataforma")

    if settings.is_production:
        try:
            verify_production_secrets()
            rel.ok("segredos e endereços públicos de produção conferidos")
        except InsecureConfiguration as erro:
            rel.impede(str(erro).splitlines()[0], conserto="veja docs/06-publicar.md")
    else:
        rel.avisa(
            f"ENVIRONMENT={settings.environment}: as verificações de segredo de "
            "produção não rodam fora de produção. Num servidor de verdade isto "
            "precisa ser 'production'."
        )

    try:
        verify_database_roles()
        rel.ok("o role da aplicação não ignora Row Level Security")
    except Exception as erro:  # noqa: BLE001
        rel.impede(
            f"role do banco com privilégio demais: {erro}",
            conserto="rode `python -m scripts.bootstrap_roles` e confira DATABASE_URL",
        )

    if not settings.unsubscribe_secret:
        rel.avisa(
            "UNSUBSCRIBE_SECRET vazio: os links de descadastro estão sendo assinados "
            "com o JWT_SECRET. Funciona, mas rotacionar o segredo das sessões — o que "
            "se faz depois de um vazamento — invalidaria todo link de descadastro já "
            "enviado, e honrar esse pedido é obrigação legal."
        )
    else:
        rel.ok("os links de descadastro têm segredo próprio, separado do JWT")

    if not settings.redis_url:
        rel.avisa(
            "sem REDIS_URL: o freio de requisições conta por processo. Basta para "
            "uma réplica; com duas, o teto anunciado passa a valer o dobro."
        )
    else:
        rel.ok("freio de requisições com balde compartilhado (Redis)")

    with unscoped_session(reason="preflight:plataforma") as session:
        admins = session.execute(
            select(User).where(User.is_platform_admin.is_(True), User.is_active.is_(True))
        ).scalars()
        quantos = len(list(admins))
    if quantos:
        rel.ok(f"{quantos} conta(s) com a marca de platform admin")
    else:
        rel.impede(
            "nenhuma conta tem a marca de platform admin: o painel da plataforma, "
            "o fechamento do mês e o suporte ficam inacessíveis",
            conserto="python -m scripts.promover_admin --email voce@suaempresa.com",
        )


def _verificar_empresas(rel: Relatorio) -> None:
    rel.titulo("Empresas")

    with unscoped_session(reason="preflight:empresas") as session:
        tenants = list(
            session.execute(select(Tenant).where(Tenant.is_active.is_(True))).scalars().all()
        )
        if not tenants:
            rel.impede(
                "nenhuma empresa ativa: ninguém tem onde entrar",
                conserto='python -m scripts.criar_empresa --nome "Sua Empresa" '
                "--email voce@suaempresa.com",
            )
            return

        for tenant in tenants:
            print(f"\n  {tenant.name} ({tenant.slug})")
            ia = ai_credentials.describe(session, tenant.id)
            if ia["configured"]:
                rel.ok(f"  chave da Anthropic configurada (status: {ia['status']})")
            elif ia["using_platform_key"]:
                rel.avisa(
                    "  sem chave própria: esta empresa está gastando na chave da "
                    "plataforma. É o interruptor entre 'o cliente paga o consumo dele' "
                    "e 'você revende tokens' — decisão comercial, não descuido."
                )
            else:
                rel.impede(
                    "  sem chave da Anthropic: os agentes não trabalham",
                    conserto="a empresa cadastra em Configurações → Inteligência artificial",
                )

            email = email_accounts.describe(session, tenant.id)
            if email["configured"] and email["status"] == "connected":
                rel.ok(f"  conta de email conectada ({email['from_email']})")
            elif email["configured"]:
                rel.impede(
                    f"  conta de email com status '{email['status']}'",
                    conserto="Configurações → Email de envio, e teste antes de salvar",
                )
            else:
                rel.impede(
                    "  sem conta de email: nenhuma abordagem sai e nenhuma resposta entra",
                    conserto="Configurações → Email de envio (Gmail, Outlook ou SMTP próprio)",
                )

            pol = retencao.politica(tenant)
            if pol.ligada:
                rel.ok(f"  descarte automático em {pol.dias} dias")
            else:
                rel.avisa(
                    "  sem prazo de descarte: nada é apagado automaticamente. É o "
                    "padrão, e é uma escolha — dado de lead guardado para sempre é "
                    "risco que cresce sozinho."
                )

            if tenant.contract_monthly_cents == 0:
                rel.avisa(
                    "  sem preço no contrato: o fechamento do mês vai mostrar o "
                    "consumo e cobrar zero."
                )
            else:
                rel.ok(
                    f"  contrato: {tenant.contract_monthly_cents / 100:.2f} "
                    f"{tenant.contract_currency} por mês"
                )


def _o_que_nao_da_para_verificar(rel: Relatorio) -> None:
    rel.titulo("O que este comando não verifica, e ainda precisa de uma pessoa")
    for linha in (
        "o backup restaura — um dump que nunca foi restaurado não é backup, é esperança",
        "o `deploy/.env` está guardado fora do servidor: sem ele, o dump não decifra "
        "nenhuma credencial das empresas",
        "o DNS aponta para este servidor e o certificado foi emitido (abra a aplicação "
        "no navegador, por HTTPS, de fora da máquina)",
        "o domínio de envio tem SPF, DKIM e DMARC — sem isso o email frio vai para spam "
        "e a reputação queima antes da primeira resposta",
        "a qualidade do que os agentes escrevem: só o primeiro disparo real mostra "
        "(docs/07-primeiro-disparo.md)",
    ):
        print(f"  · {linha}")


def main(argv: list[str] | None = None) -> int:
    rel = Relatorio()
    print("Pré-voo do go-live")
    _verificar_plataforma(rel)
    _verificar_empresas(rel)
    _o_que_nao_da_para_verificar(rel)

    print()
    if rel.impedimentos:
        print(
            f"{VERMELHO}{rel.impedimentos} item(ns) impedem a operação{FIM}"
            + (f", {rel.avisos} aviso(s)" if rel.avisos else "")
        )
        return 1
    if rel.avisos:
        print(f"{VERDE}nada impede a operação{FIM} — {rel.avisos} aviso(s) para conferir")
    else:
        print(f"{VERDE}pronto para operar{FIM}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
