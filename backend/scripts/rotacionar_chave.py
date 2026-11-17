"""Rotaciona a chave de cifra sem perder o que já está salvo.

A sequência, e ela importa:

1. Gere a chave nova:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. No `.env`: `SECRETS_ENCRYPTION_KEY` recebe a **nova**, e
   `SECRETS_ENCRYPTION_KEYS_PREVIOUS` recebe a **antiga** (várias, se houver, separadas
   por vírgula).
3. Suba a aplicação. Nada quebra: a nova cifra, a antiga ainda decifra.
4. Rode este script: `python -m scripts.rotacionar_chave`
5. Confira: `python -m scripts.rotacionar_chave --verificar`
6. Só então apague `SECRETS_ENCRYPTION_KEYS_PREVIOUS` e suba de novo.

Fazer o passo 6 antes do 4 é o que torna as credenciais ilegíveis — e o
`--verificar` existe para que essa decisão não seja um palpite.

Roda com o role administrativo porque atravessa todos os tenants: é manutenção de
plataforma, não operação de cliente. O conteúdo dos segredos não passa por
nenhuma variável daqui — `recifrar` usa `MultiFernet.rotate`.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from app.core.crypto import SecretDecryptionError, recifrar, rotacao_pendente
from app.core.segredos_guardados import COLUNAS_CIFRADAS
from app.db.session import AdminSessionFactory


def _linhas(session, coluna) -> list[tuple]:
    sql = text(
        f"SELECT {coluna.chave}, {coluna.coluna} FROM {coluna.tabela} "  # noqa: S608
        f"WHERE {coluna.coluna} IS NOT NULL"
    )
    return list(session.execute(sql).all())


def rotacionar(*, apenas_verificar: bool = False) -> int:
    total = 0
    problemas = 0

    with AdminSessionFactory() as session:
        for coluna in COLUNAS_CIFRADAS:
            linhas = _linhas(session, coluna)
            print(f"{coluna.tabela}.{coluna.coluna}: {len(linhas)} segredo(s)")
            for chave, valor in linhas:
                try:
                    novo = recifrar(valor)
                except SecretDecryptionError as exc:
                    problemas += 1
                    print(f"  ✗ {coluna.tabela} {chave}: {exc}", file=sys.stderr)
                    continue
                if apenas_verificar:
                    total += 1
                    continue
                session.execute(
                    text(
                        f"UPDATE {coluna.tabela} SET {coluna.coluna} = :v "  # noqa: S608
                        f"WHERE {coluna.chave} = :k"
                    ),
                    {"v": novo, "k": chave},
                )
                total += 1
        if not apenas_verificar:
            session.commit()

    if problemas:
        print(
            f"\n✗ {problemas} segredo(s) não abriram com nenhuma chave configurada. "
            "NÃO apague a chave antiga: ponha todas as chaves já usadas em "
            "SECRETS_ENCRYPTION_KEYS_PREVIOUS e rode de novo.",
            file=sys.stderr,
        )
        return 1

    if apenas_verificar:
        print(f"\n✓ {total} segredo(s) abrem com as chaves configuradas.")
        if rotacao_pendente():
            print(
                "  Ainda há chave antiga na configuração. Se este comando rodou depois "
                "da rotação, agora é seguro apagar SECRETS_ENCRYPTION_KEYS_PREVIOUS."
            )
        return 0

    print(f"\n✓ {total} segredo(s) recifrados com a chave atual.")
    print("  Confira com `--verificar` e só então apague a chave antiga do .env.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="Só confere que todo segredo abre; não escreve nada.",
    )
    args = parser.parse_args()
    return rotacionar(apenas_verificar=args.verificar)


if __name__ == "__main__":
    raise SystemExit(main())
