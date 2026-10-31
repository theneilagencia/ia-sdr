"""Promove um usuário a administrador da plataforma.

É um script de linha de comando, e não uma rota, por um motivo de galinha e ovo:
as quatro rotas do painel exigem um administrador de plataforma para responder,
então o primeiro deles não pode nascer de uma rota autenticada por ele mesmo.
Quem roda isto já tem acesso ao servidor — o que é exatamente o nível de
privilégio que a operação merece.

    python -m scripts.promover_admin --listar
    python -m scripts.promover_admin --email voce@apymine.com
    python -m scripts.promover_admin --email antigo@apymine.com --revogar

**O que a marca concede, e o que não concede.** O administrador de plataforma
enxerga a lista de empresas, o consumo agregado e a saúde do sistema, e pode
mexer em plano, status de assinatura e limites. O que ele **não** ganha é acesso
ao dado comercial de uma empresa da qual não é membro: as rotas normais
continuam exigindo vínculo, e o Row Level Security continua filtrando. Ver a
conta de um cliente e ler as conversas dele são coisas diferentes, e só a
primeira está aqui.
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from app.db.models.platform import Membership, User
from app.db.session import unscoped_session

logger = logging.getLogger("ia_sdr.platform_admin")


def _linha(user: User, empresas: int) -> str:
    return (
        f"  {user.email:40} {user.full_name or '(sem nome)':20} "
        f"{empresas} empresa(s){'' if user.is_active else '  [inativo]'}"
    )


def listar() -> int:
    with unscoped_session(reason="platform-admin:listar") as session:
        admins = list(
            session.execute(
                select(User).where(User.is_platform_admin.is_(True)).order_by(User.email)
            ).scalars()
        )
        if not admins:
            print("Nenhum administrador de plataforma. O painel está inalcançável.")
            print("Para criar o primeiro: --email <email de um usuário existente>")
            return 0
        print(f"{len(admins)} administrador(es) de plataforma:")
        for admin in admins:
            empresas = session.execute(
                select(Membership).where(Membership.user_id == admin.id)
            ).scalars()
            print(_linha(admin, len(list(empresas))))
    return 0


def definir(email: str, *, promover: bool, motivo: str | None) -> int:
    alvo = email.strip().lower()
    with unscoped_session(reason="platform-admin:promover") as session:
        user = session.execute(select(User).where(User.email == alvo)).scalar_one_or_none()
        if user is None:
            print(f"✗ não existe usuário com o email '{alvo}'.")
            print("  A promoção não cria conta: crie a empresa e o usuário primeiro")
            print("  (python -m scripts.criar_empresa) e promova depois.")
            return 1

        if user.is_platform_admin == promover:
            estado = "já é" if promover else "já não é"
            print(f"· {alvo} {estado} administrador de plataforma. Nada a fazer.")
            return 0

        user.is_platform_admin = promover
        session.flush()

    # A trilha de auditoria da aplicação é por empresa, e esta ação não pertence
    # a nenhuma — então ela fica no log do processo, que no servidor é onde o
    # registro de quem mexeu no que já vive.
    logger.warning(
        "platform_admin.%s email=%s motivo=%s",
        "granted" if promover else "revoked",
        alvo,
        motivo or "(não informado)",
    )
    if promover:
        print(f"✓ {alvo} agora é administrador de plataforma.")
        print("  Enxerga empresas, consumo e saúde do sistema, e mexe em plano e limites.")
        print("  NÃO ganha acesso ao dado comercial de empresa da qual não é membro.")
        print("  Vale para o token que já está na mão: não precisa entrar de novo.")
    else:
        print(f"✓ {alvo} não é mais administrador de plataforma.")
        print("  Vale no ato: o próximo pedido ao painel já é recusado.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", help="Email de um usuário que já existe")
    parser.add_argument("--revogar", action="store_true", help="Tira a marca em vez de conceder")
    parser.add_argument("--listar", action="store_true", help="Mostra quem já tem a marca")
    parser.add_argument("--motivo", help="Vai para o log, ao lado de quem foi promovido")
    args = parser.parse_args(argv)

    if args.listar:
        return listar()
    if not args.email:
        parser.print_help()
        print("\nInforme --email, ou --listar para ver quem já tem a marca.")
        return 1
    return definir(args.email, promover=not args.revogar, motivo=args.motivo)


if __name__ == "__main__":
    sys.exit(main())
