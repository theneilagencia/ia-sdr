"""Segredo vindo de arquivo: o que torna um gerenciador externo utilizável.

Vault com agente, AWS Secrets Manager via ECS, Kubernetes Secret montado, Docker
secret e systemd credentials entregam segredo do mesmo jeito: um arquivo no
disco. Aceitar `<NOME>_FILE` é, portanto, compatibilidade com todos eles sem
depender de nenhum — e sem SDK de fornecedor dentro desta aplicação.

O teste que mais importa aqui é o do erro: um `_FILE` apontando para caminho que
não existe **não pode** cair no valor padrão em silêncio. O padrão é o segredo de
desenvolvimento que está publicado no repositório, e uma aplicação que sobe com
ele funciona perfeitamente — enquanto qualquer pessoa que leu o código assina
token válido para qualquer empresa.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings, _valores_de_arquivo, get_settings

CAMPOS = set(Settings.model_fields)


def test_o_conteudo_do_arquivo_vale_como_valor(tmp_path, monkeypatch):
    arquivo = tmp_path / "jwt"
    arquivo.write_text("segredo-que-veio-do-vault")
    monkeypatch.setenv("JWT_SECRET_FILE", str(arquivo))

    assert _valores_de_arquivo(CAMPOS) == {"JWT_SECRET": "segredo-que-veio-do-vault"}


def test_a_quebra_de_linha_do_echo_e_removida(tmp_path, monkeypatch):
    """`echo segredo > arquivo` deixa um \\n, e segredo com \\n invisível é o pior bug.

    Não falha na subida: falha depois, na comparação, como "a senha está errada e
    ninguém sabe por quê".
    """
    arquivo = tmp_path / "chave"
    arquivo.write_text("chave-com-quebra\n")
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY_FILE", str(arquivo))

    assert _valores_de_arquivo(CAMPOS)["SECRETS_ENCRYPTION_KEY"] == "chave-com-quebra"


def test_arquivo_que_nao_existe_derruba_em_vez_de_cair_no_padrao(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_FILE", "/run/secrets/que-nao-existe")

    with pytest.raises(RuntimeError) as erro:
        _valores_de_arquivo(CAMPOS)

    assert "JWT_SECRET_FILE" in str(erro.value)
    assert "não existe" in str(erro.value)


def test_arquivo_vazio_tambem_derruba(tmp_path, monkeypatch):
    """Segredo montado e vazio é o que acontece quando o vault não injetou nada."""
    vazio = tmp_path / "vazio"
    vazio.write_text("   \n")
    monkeypatch.setenv("JWT_SECRET_FILE", str(vazio))

    with pytest.raises(RuntimeError, match="vazio"):
        _valores_de_arquivo(CAMPOS)


def test_variavel_de_outra_ferramenta_e_ignorada(monkeypatch):
    """O defeito que a primeira versão tinha, encontrado na primeira execução.

    A varredura pegava toda variável terminada em `_FILE`, e o ambiente onde isto
    foi escrito tinha um `CLAUDE_CODE_ARTIFACT_MULTI_FILE=1` de outra ferramenta:
    a aplicação morria na importação da configuração, com um erro que não tinha
    nada a ver com a causa. Aplicação que não sobe por causa de variável alheia é
    péssima vizinha.
    """
    monkeypatch.setenv("FERRAMENTA_ALHEIA_MULTI_FILE", "1")
    monkeypatch.setenv("QUALQUER_COISA_FILE", "/caminho/que/nao/existe")

    assert _valores_de_arquivo(CAMPOS) == {}


def test_o_valor_do_arquivo_vence_a_variavel_de_ambiente(tmp_path, monkeypatch):
    """Quem montou o segredo num arquivo quis usá-lo.

    O contrário — variável vencendo arquivo — faria uma variável esquecida no
    compose silenciosamente ignorar o vault, que é o tipo de coisa que só se
    descobre investigando por que a rotação "não pegou".
    """
    arquivo = tmp_path / "jwt"
    arquivo.write_text("o-do-arquivo")
    monkeypatch.setenv("JWT_SECRET", "o-do-ambiente")
    monkeypatch.setenv("JWT_SECRET_FILE", str(arquivo))
    get_settings.cache_clear()

    try:
        assert get_settings().jwt_secret == "o-do-arquivo"
    finally:
        get_settings.cache_clear()


def test_sem_nenhum_file_nada_muda(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_FILE", raising=False)
    assert _valores_de_arquivo(CAMPOS) == {}


def test_serve_para_qualquer_campo_nao_so_segredo(tmp_path, monkeypatch):
    """Sem lista fechada de campos: a URL do banco também carrega senha dentro.

    Uma lista de "campos secretos" mantida à mão esqueceria justamente o próximo
    campo sensível a ser criado.
    """
    arquivo = tmp_path / "url"
    arquivo.write_text("postgresql+psycopg://app:senha@db:5432/ia_sdr")
    monkeypatch.setenv("DATABASE_URL_FILE", str(arquivo))

    assert _valores_de_arquivo(CAMPOS)["DATABASE_URL"].endswith("/ia_sdr")
