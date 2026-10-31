"""As migrations e os modelos descrevem o mesmo banco.

Deriva entre os dois é uma falha que não aparece em nenhum teste de
comportamento: o modelo ganha uma coluna, ninguém escreve a migration, a suíte
passa porque o banco de teste é criado a partir das migrations... e em produção a
coluna não existe. O sintoma é um `UndefinedColumn` no meio de uma requisição,
horas depois do deploy.

O `compare_metadata` do Alembic responde exatamente essa pergunta: o que um
`--autogenerate` escreveria agora? Se a resposta não for "nada", falta migration.
"""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db.models import Base
from app.db.session import admin_engine


def test_modelos_e_migrations_nao_divergem():
    with admin_engine.connect() as conexao:
        contexto = MigrationContext.configure(conexao, opts={"compare_type": True})
        diferencas = compare_metadata(contexto, Base.metadata)

    legiveis = [repr(d)[:200] for d in diferencas]
    assert diferencas == [], (
        "o banco migrado não é o que os modelos descrevem — falta migration:\n"
        + "\n".join(legiveis)
    )


def test_a_ordem_das_migrations_e_uma_linha_so():
    """Duas migrations com o mesmo `down_revision` criam um ramo.

    Ramo em migration é o tipo de coisa que passa no CI de quem abriu o PR e
    quebra na subida de quem faz o merge: o Alembic recusa `upgrade head` quando
    existe mais de uma cabeça, e a mensagem não diz qual par colidiu.
    """
    from pathlib import Path

    versoes = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    anteriores: dict[str, list[str]] = {}
    revisoes: set[str] = set()
    for arquivo in versoes.glob("*.py"):
        texto = arquivo.read_text()
        revisao = _valor(texto, "revision")
        anterior = _valor(texto, "down_revision")
        assert revisao, f"{arquivo.name} sem revision"
        assert revisao not in revisoes, f"revision repetida: {revisao}"
        revisoes.add(revisao)
        anteriores.setdefault(anterior or "", []).append(arquivo.name)

    ramos = {anterior: nomes for anterior, nomes in anteriores.items() if len(nomes) > 1}
    assert ramos == {}, f"migrations que partem do mesmo ponto: {ramos}"


def _valor(texto: str, nome: str) -> str | None:
    for linha in texto.splitlines():
        if linha.startswith(f"{nome} = "):
            bruto = linha.split("=", 1)[1].strip()
            if bruto == "None":
                return None
            return bruto.strip("\"'")
    return None


def test_cada_erro_da_api_tem_um_codigo_proprio():
    """Dois erros com o mesmo código são um erro só para quem consome a API.

    Foi o caso de `document_too_large`, usado ao mesmo tempo para "o texto colado
    é longo demais" e "o arquivo subido passa do teto" — dois problemas com duas
    soluções diferentes. O teste vive neste arquivo porque é da mesma família:
    propriedade que ninguém verifica de olho.
    """
    import importlib
    import pkgutil

    import app
    from app.core.errors import AppError

    for modulo in pkgutil.walk_packages(app.__path__, "app."):
        try:
            importlib.import_module(modulo.name)
        except Exception:  # noqa: BLE001 - módulo opcional não invalida a varredura
            continue

    def descendentes(classe):
        for sub in classe.__subclasses__():
            yield sub
            yield from descendentes(sub)

    por_codigo: dict[str, list[str]] = {}
    for sub in descendentes(AppError):
        por_codigo.setdefault(getattr(sub, "code", ""), []).append(sub.__name__)

    repetidos = {c: nomes for c, nomes in por_codigo.items() if len(nomes) > 1}
    assert repetidos == {}, f"códigos de erro repetidos: {repetidos}"
