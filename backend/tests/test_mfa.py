"""Segundo fator por TOTP: o que ele exige, e o que ele não pode deixar passar.

O valor deste arquivo não está no caminho feliz — está nas recusas. Um
segundo fator que aceita o mesmo código duas vezes, ou que pode ser contornado
por outra porta da aplicação, é enfeite de tela de configurações.

Os vetores da RFC ficam aqui também: a implementação é nossa (`app/core/totp.py`,
biblioteca padrão de propósito), e o que sustenta essa decisão é justamente o
teste que compara com os números publicados na especificação.
"""

from __future__ import annotations

import base64

import pytest

from app.core import totp
from app.core.crypto import decrypt_secret
from app.db.models.platform import User
from app.db.session import unscoped_session
from app.services import mfa

# RFC 6238, seção 5.4 (SHA-1) — segredo "12345678901234567890".
SEGREDO_RFC = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
VETORES = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
    (20000000000, "353130"),
]


def _segredo_de(email: str) -> str:
    with unscoped_session(reason="test:ler-segredo") as session:
        from sqlalchemy import select

        user = session.execute(select(User).where(User.email == email)).scalar_one()
        assert user.mfa_secret is not None
        # Cifrado no banco: o que está lá não é o segredo.
        assert "=" not in user.mfa_secret[:10]
        return decrypt_secret(user.mfa_secret)


def _proximo_codigo(email: str) -> None:
    """Simula a pessoa esperando o próximo código do aplicativo.

    Cada passo de trinta segundos serve **uma vez**: confirmar o segundo fator
    gasta o passo em que o código foi digitado, e o mesmo código não entra no
    login logo depois — que é a propriedade que recusa reuso, não um defeito.

    Esperar de verdade custaria meio minuto por teste, e esperar na borda da
    janela produz falha por sorteio. Zerar o último passo aceito é o equivalente
    honesto: é o que acontece quando o aplicativo mostra o número seguinte.
    """
    with unscoped_session(reason="test:proximo-passo") as session:
        from sqlalchemy import select

        user = session.execute(select(User).where(User.email == email)).scalar_one()
        user.mfa_last_step = None


def _ligar(client, headers, email: str) -> tuple[str, list[str]]:
    """Configura e liga o segundo fator, devolvendo segredo e códigos de recuperação."""
    setup = client.post("/api/v1/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    segredo = setup.json()["secret"]
    confirmado = client.post(
        "/api/v1/auth/mfa/confirm",
        headers=headers,
        json={"code": totp.codigo_agora(segredo)},
    )
    assert confirmado.status_code == 200, confirmado.text
    return segredo, confirmado.json()["recovery_codes"]


# ------------------------------------------------------------ o algoritmo


@pytest.mark.parametrize(("instante", "esperado"), VETORES)
def test_os_vetores_da_rfc_batem(instante, esperado):
    """A razão para não trazer uma dependência: a especificação é verificável.

    Se algum dia estes números divergirem, o segundo fator de todo mundo para de
    funcionar ao mesmo tempo — e é melhor descobrir aqui do que na tela de login.
    """
    assert totp.codigo_agora(SEGREDO_RFC, agora=instante) == esperado


def test_o_relogio_atrasado_alguns_segundos_ainda_entra():
    """Recusar por trinta segundos de diferença ensina a desligar o segundo fator."""
    agora = 1_700_000_000
    codigo = totp.codigo_agora(SEGREDO_RFC, agora=agora)
    assert totp.passo_valido(SEGREDO_RFC, codigo, agora=agora + 25) is not None
    assert totp.passo_valido(SEGREDO_RFC, codigo, agora=agora - 25) is not None
    # Duas janelas de distância já não: a tolerância tem limite.
    assert totp.passo_valido(SEGREDO_RFC, codigo, agora=agora + 95) is None


def test_codigo_fora_de_forma_nao_passa():
    for ruim in ("", "12345", "1234567", "abcdef", "12 34 56 78"):
        assert totp.passo_valido(SEGREDO_RFC, ruim) is None


# ------------------------------------------------------------ ligar e desligar


def test_o_segredo_aparece_uma_vez_e_o_banco_guarda_cifrado(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])

    setup = client.post("/api/v1/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    segredo = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    assert segredo in setup.json()["otpauth_uri"]
    # O que está no banco não é o segredo em claro.
    assert _segredo_de(t["email"]) == segredo

    # E gerar segredo **não liga nada**: a senha sozinha ainda entra.
    estado = client.get("/api/v1/auth/mfa", headers=headers).json()
    assert estado["enabled"] is False and estado["pending"] is True
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": t["email"], "password": t["password"]}
        ).status_code
        == 200
    )


def test_confirmar_liga_e_devolve_os_codigos_de_recuperacao(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _, codigos = _ligar(client, headers, t["email"])

    assert len(codigos) == mfa.RECUPERACAO_QUANTOS
    assert len(set(codigos)) == len(codigos)
    estado = client.get("/api/v1/auth/mfa", headers=headers).json()
    assert estado["enabled"] is True
    assert estado["recovery_codes_left"] == mfa.RECUPERACAO_QUANTOS
    # A listagem de estado não devolve segredo nem código nenhum.
    assert "secret" not in estado and "recovery_codes" not in estado


def test_o_codigo_que_ligou_o_fator_nao_serve_para_entrar(client, make_tenant, auth_headers):
    """Confirmar gasta o passo: o código da configuração não vale no login.

    Importa porque a tela de configuração mostra o número e a pessoa pode
    repeti-lo segundos depois — e porque é a mesma propriedade que impede quem
    leu o código por cima do ombro de usá-lo enquanto a janela não virou.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    segredo, _ = _ligar(client, headers, t["email"])

    reuso = client.post(
        "/api/v1/auth/login",
        json={"email": t["email"], "password": t["password"], "code": totp.codigo_agora(segredo)},
    )
    assert reuso.status_code == 401
    assert reuso.json()["error"]["code"] == "mfa_invalid"


def test_codigo_errado_nao_liga(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    client.post("/api/v1/auth/mfa/setup", headers=headers)
    r = client.post("/api/v1/auth/mfa/confirm", headers=headers, json={"code": "000000"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "mfa_invalid"
    assert client.get("/api/v1/auth/mfa", headers=headers).json()["enabled"] is False


def test_nao_se_troca_o_segundo_fator_com_a_sessao_aberta(client, make_tenant, auth_headers):
    """Token roubado não pode apontar o segundo fator para outro celular."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _ligar(client, headers, t["email"])

    r = client.post("/api/v1/auth/mfa/setup", headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "mfa_already_active"


def test_desligar_exige_senha_e_codigo(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    segredo, _ = _ligar(client, headers, t["email"])

    # Só o código não basta.
    so_codigo = client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"password": "senha-errada-mas-longa", "code": totp.codigo_agora(segredo)},
    )
    assert so_codigo.status_code == 401
    assert client.get("/api/v1/auth/mfa", headers=headers).json()["enabled"] is True

    # Só a senha também não.
    so_senha = client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"password": t["password"], "code": "000000"},
    )
    assert so_senha.status_code == 401

    # As duas, sim — e com um código que ainda não foi gasto.
    _proximo_codigo(t["email"])
    ok = client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"password": t["password"], "code": totp.codigo_agora(segredo)},
    )
    assert ok.status_code == 204, ok.text
    assert client.get("/api/v1/auth/mfa", headers=headers).json()["enabled"] is False


# ------------------------------------------------------------ o login


def test_com_segundo_fator_a_senha_sozinha_nao_entra(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    segredo, _ = _ligar(client, headers, t["email"])
    _proximo_codigo(t["email"])

    sem_codigo = client.post(
        "/api/v1/auth/login", json={"email": t["email"], "password": t["password"]}
    )
    assert sem_codigo.status_code == 401
    assert sem_codigo.json()["error"]["code"] == "mfa_required"

    com_codigo = client.post(
        "/api/v1/auth/login",
        json={"email": t["email"], "password": t["password"], "code": totp.codigo_agora(segredo)},
    )
    assert com_codigo.status_code == 200, com_codigo.text
    assert com_codigo.json()["access_token"]


def test_senha_errada_nao_conta_que_existe_segundo_fator(client, make_tenant, auth_headers):
    """A ordem importa: o segundo fator é dito só a quem provou a senha.

    Se `mfa_required` viesse antes da senha, esta rota diria a qualquer um quais
    contas existem e quais estão protegidas — um mapa para quem procura alvo.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _ligar(client, headers, t["email"])

    errada = client.post(
        "/api/v1/auth/login", json={"email": t["email"], "password": "chute-longo-mas-errado"}
    )
    assert errada.status_code == 401
    assert errada.json()["error"]["code"] != "mfa_required"
    # E a mesma resposta de um email que não existe.
    inexistente = client.post(
        "/api/v1/auth/login",
        json={"email": "ninguem-aqui@example.com", "password": "chute-longo-mas-errado"},
    )
    assert errada.json()["error"]["message"] == inexistente.json()["error"]["message"]


def test_o_mesmo_codigo_nao_entra_duas_vezes(client, make_tenant, auth_headers):
    """Código interceptado no caminho vale trinta segundos para quem o pegou.

    Sem recusar reuso, quem lê o código na tela de alguém (ou num log, ou num
    print de tela pedido por telefone) entra logo depois com ele.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    segredo, _ = _ligar(client, headers, t["email"])
    _proximo_codigo(t["email"])
    codigo = totp.codigo_agora(segredo)

    primeiro = client.post(
        "/api/v1/auth/login",
        json={"email": t["email"], "password": t["password"], "code": codigo},
    )
    assert primeiro.status_code == 200, primeiro.text
    segundo = client.post(
        "/api/v1/auth/login",
        json={"email": t["email"], "password": t["password"], "code": codigo},
    )
    assert segundo.status_code == 401
    assert segundo.json()["error"]["code"] == "mfa_invalid"


def test_codigo_de_recuperacao_entra_uma_vez(client, make_tenant, auth_headers):
    """Perder o celular não pode significar perder a empresa."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    _, codigos = _ligar(client, headers, t["email"])
    resgate = codigos[0]

    entrou = client.post(
        "/api/v1/auth/login",
        json={"email": t["email"], "password": t["password"], "code": resgate},
    )
    assert entrou.status_code == 200, entrou.text
    de_novo = client.post(
        "/api/v1/auth/login",
        json={"email": t["email"], "password": t["password"], "code": resgate},
    )
    assert de_novo.status_code == 401

    novos = {"Authorization": f"Bearer {entrou.json()['access_token']}"}
    assert (
        client.get("/api/v1/auth/mfa", headers=novos).json()["recovery_codes_left"]
        == mfa.RECUPERACAO_QUANTOS - 1
    )


def test_cinco_chutes_e_a_conta_descansa(client, make_tenant, auth_headers):
    """Seis dígitos são um milhão de combinações — e o contador tem de sobreviver.

    O contador é incrementado na transação do próprio serviço de MFA, e não na de
    quem chama, justamente porque a recusa é uma exceção: dentro da transação de
    quem chama, cada chute errado seria desfeito junto com a recusa e o bloqueio
    nunca chegaria a cinco.
    """
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    segredo, _ = _ligar(client, headers, t["email"])

    for _ in range(mfa.TENTATIVAS):
        r = client.post(
            "/api/v1/auth/login",
            json={"email": t["email"], "password": t["password"], "code": "000000"},
        )
        assert r.status_code == 401

    # Bloqueada: agora nem o código certo entra.
    bloqueada = client.post(
        "/api/v1/auth/login",
        json={"email": t["email"], "password": t["password"], "code": totp.codigo_agora(segredo)},
    )
    assert bloqueada.status_code == 429
    assert bloqueada.json()["error"]["code"] == "mfa_locked"


def test_quem_nao_tem_segundo_fator_entra_como_sempre(client, make_tenant):
    t = make_tenant()
    r = client.post("/api/v1/auth/login", json={"email": t["email"], "password": t["password"]})
    assert r.status_code == 200, r.text


# ------------------------------------------- a porta que contornaria o MFA


def test_o_convite_nao_contorna_o_segundo_fator(client, make_tenant, auth_headers):
    """O aceite emite sessão, então exige o mesmo fator que o login.

    Sem isto, qualquer empresa poderia convidar o email de alguém que tem segundo
    fator ligado, entrar com a senha (que pode ter vazado — é justamente por isso
    que o segundo fator existe) e receber uma sessão sem código nenhum.
    """
    dona = make_tenant()
    outra = make_tenant()
    protegida = auth_headers(dona["email"], dona["password"])
    segredo, _ = _ligar(client, protegida, dona["email"])
    _proximo_codigo(dona["email"])

    convite = client.post(
        "/api/v1/tenants/me/invitations",
        headers=auth_headers(outra["email"], outra["password"]),
        json={"email": dona["email"], "role": "operator"},
    )
    assert convite.status_code == 201, convite.text
    token = convite.json()["accept_url"].rsplit("/", 1)[-1]

    sem_codigo = client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": dona["password"], "full_name": ""},
    )
    assert sem_codigo.status_code == 401
    assert sem_codigo.json()["error"]["code"] == "mfa_required"

    # E o convite **não** foi gasto: erro de código não pode trancar ninguém fora.
    com_codigo = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": token,
            "password": dona["password"],
            "full_name": "",
            "code": totp.codigo_agora(segredo),
        },
    )
    assert com_codigo.status_code == 200, com_codigo.text
    assert com_codigo.json()["tenant_id"] == str(outra["tenant_id"])


def test_conta_nova_por_convite_nao_pede_codigo(client, make_tenant, auth_headers):
    """Quem ainda não tem conta não tem segundo fator para provar."""
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    convite = client.post(
        "/api/v1/tenants/me/invitations",
        headers=headers,
        json={"email": "nova-pessoa@example.com", "role": "viewer"},
    )
    token = convite.json()["accept_url"].rsplit("/", 1)[-1]
    r = client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "senha-escolhida-123", "full_name": "Nova"},
    )
    assert r.status_code == 200, r.text


def test_o_segundo_fator_fica_na_auditoria(client, make_tenant, auth_headers):
    t = make_tenant()
    headers = auth_headers(t["email"], t["password"])
    segredo, _ = _ligar(client, headers, t["email"])
    acoes = [e["action"] for e in client.get("/api/v1/tenants/me/audit", headers=headers).json()]
    assert "mfa.enabled" in acoes

    _proximo_codigo(t["email"])
    client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"password": t["password"], "code": totp.codigo_agora(segredo)},
    )
    acoes = [e["action"] for e in client.get("/api/v1/tenants/me/audit", headers=headers).json()]
    assert "mfa.disabled" in acoes
