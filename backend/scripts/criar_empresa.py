"""Cria a primeira empresa de um ambiente de produção.

O `seed_demo` existe para ver o isolamento funcionando e planta dado fictício;
não serve para produção. Este script cria só o que é necessário para alguém
entrar na aplicação e começar a configurar: a empresa, o usuário owner e o
perfil vazio do Company Brain.

    python -m scripts.criar_empresa --nome "Apy Mine" --email vinicius@apymine.com

A senha é gerada e impressa uma vez. Não fica em variável de ambiente, não vai
para log e não é recuperável — quem receber troca no primeiro acesso.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from app.core.security import hash_password
from app.db.models.knowledge import CompanyProfile
from app.db.models.platform import Membership, Plan, Tenant, User
from app.db.session import tenant_session, unscoped_session

#: Comprimento da senha gerada. 20 caracteres de alfabeto urlsafe passam de
#: 100 bits de entropia — não precisa de política de complexidade.
TAMANHO_SENHA = 20


def _slug(nome: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")
    return base[:80] or "empresa"


def _email_utilizavel(bruto: str) -> str | None:
    """O email normalizado como o login vai procurá-lo, ou None com o motivo impresso.

    Duas coisas que só apareciam no primeiro acesso do cliente, longe da causa:
    o login compara `email.lower()` com o que está guardado, então maiúscula
    gravada aqui virava "Email ou senha inválidos" para sempre — a mesma frase de
    senha errada, porque ela é única de propósito; e a API valida email com o
    mesmo `email_validator` do Pydantic, que recusa TLD reservado (`.test`,
    `.local`, `.localhost`). Criar a empresa e descobrir no login que o endereço
    não serve é fazer o cliente pagar pelo erro de digitação de quem provisionou.
    """
    email = bruto.strip().lower()
    try:
        # `check_deliverability=False`: provisionar não é hora de depender de DNS,
        # e a API valida do mesmo jeito. O que se quer aqui é o formato.
        validate_email(email, check_deliverability=False)
    except EmailNotValidError as erro:
        print(f"✗ o email '{bruto}' não serve para entrar na aplicação: {erro}")
        print("  Corrija o endereço e rode de novo — nada foi criado.")
        return None
    return email


def criar(
    *, nome: str, email: str, slug: str | None, plano: str, nome_completo: str
) -> tuple[str, str, str] | None:
    """Devolve (slug, senha, email) ou None se já existir. Não sobrescreve nada."""
    slug = slug or _slug(nome)
    senha = secrets.token_urlsafe(TAMANHO_SENHA)

    normalizado = _email_utilizavel(email)
    if normalizado is None:
        return None
    email = normalizado

    with unscoped_session(reason="provisionamento:primeira-empresa") as session:
        if session.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none():
            print(f"✗ já existe uma empresa com o slug '{slug}'. Use --slug para outro.")
            return None
        existente = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existente:
            # Reusar o usuário mudaria a senha de alguém que já entra na
            # plataforma. Melhor falhar e deixar a decisão para uma pessoa.
            print(
                f"✗ o email '{email}' já tem usuário. Crie a empresa com outro "
                "email e depois convide este pela tela de membros."
            )
            return None

        tenant = Tenant(name=nome, slug=slug, plan=plano)
        user = User(email=email, password_hash=hash_password(senha), full_name=nome_completo)
        session.add_all([tenant, user])
        session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role="owner"))
        session.flush()
        tenant_id = tenant.id

    # O perfil nasce vazio de propósito: o conteúdo do Company Brain é a
    # empresa que escreve, e um texto inventado aqui viraria contexto de
    # agente sem ninguém ter revisado.
    with tenant_session(tenant_id) as session:
        session.add(CompanyProfile(tenant_id=tenant_id, legal_name=nome))

    return slug, senha, email


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cria empresa e usuário owner.")
    parser.add_argument("--nome", required=True, help="Nome da empresa")
    parser.add_argument("--email", required=True, help="Email do owner")
    parser.add_argument("--slug", help="Identificador na URL (derivado do nome)")
    parser.add_argument("--nome-completo", default="Owner", help="Nome do owner")
    parser.add_argument(
        "--plano",
        default=Plan.STARTER.value,
        choices=[p.value for p in Plan],
        help="Plano inicial",
    )
    args = parser.parse_args(argv)

    resultado = criar(
        nome=args.nome,
        email=args.email,
        slug=args.slug,
        plano=args.plano,
        nome_completo=args.nome_completo,
    )
    if resultado is None:
        return 1

    slug, senha, email = resultado
    print(f"✓ empresa '{args.nome}' criada (slug: {slug}, plano: {args.plano})")
    print()
    # O email impresso é o normalizado, não o digitado: é ele que o login aceita.
    print(f"  login: {email}")
    print(f"  senha: {senha}")
    print()
    print("Esta senha não é recuperável e não foi gravada em lugar nenhum além")
    print("do hash. Entregue por um canal seguro e troque no primeiro acesso.")
    print("Próximo passo, já na aplicação: Configurações → chave da Anthropic e")
    print("conta de email da empresa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
