"""Onde texto cifrado mora no banco — declarado, porque esquecer custa dado.

Uma rotação de chave que deixa uma coluna para trás não falha: ela apaga, em
silêncio, a única cópia daquele segredo. Quando a chave antiga sair da
configuração, o que ficou sem recifrar vira ilegível para sempre — e nada avisa,
porque decifrar só acontece quando alguém tenta usar a credencial, semanas
depois, no meio de um disparo.

Então a lista é explícita e tem tripwire: `tests/test_rotacao_de_chave.py` procura
no código todo lugar que escreve com `encrypt_secret`/`encrypt_json` e falha se o
alvo não estiver aqui. É o mesmo desenho de `TENANT_SCOPED_TABLES` — a lista que
o RLS usa —, pelo mesmo motivo: o que precisa estar completo não pode depender de
alguém lembrar.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ColunaCifrada:
    tabela: str
    coluna: str
    #: A chave primária, para o script conseguir atualizar linha a linha.
    chave: str = "id"


#: Toda coluna que guarda texto cifrado com a chave Fernet da plataforma.
COLUNAS_CIFRADAS: tuple[ColunaCifrada, ...] = (
    # Credenciais de integração: senha de email, chave da Anthropic, token do RAVI.
    ColunaCifrada(tabela="integrations", coluna="credentials_encrypted"),
    # Segredo do segundo fator, que é credencial de acesso à própria plataforma.
    ColunaCifrada(tabela="users", coluna="mfa_secret"),
)

__all__ = ["COLUNAS_CIFRADAS", "ColunaCifrada"]
