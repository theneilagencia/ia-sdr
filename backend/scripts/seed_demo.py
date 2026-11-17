"""Popula dois tenants de demonstração.

Serve para ver o isolamento funcionando: o mesmo motor, dois negócios
completamente diferentes, nenhum dado em comum.

    python -m scripts.seed_demo
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models.engagement import Conversation, Message
from app.db.models.knowledge import CompanyProfile, KnowledgeChunk, KnowledgeDocument
from app.db.models.platform import Membership, Plan, Tenant, User
from app.db.models.sales import Campaign, Company, Contact, Prospect
from app.db.session import tenant_session, unscoped_session

DEMOS = [
    {
        "tenant": "Apy Mine",
        "slug": "apy-mine",
        "plan": Plan.GROWTH.value,
        "email": "owner@apymine.com",
        "positioning": "Software de gestão para operações de mineração",
        "campaigns": [
            ("Mining Canada", "mining-canada", ["CA"], "CFO"),
            ("Mining Brazil", "mining-brazil", ["BR"], "COO"),
        ],
        "doc": "Playbook de vendas para mineradoras de médio porte.",
        "rascunho": True,
    },
    {
        "tenant": "Empresa XYZ",
        "slug": "empresa-xyz",
        "plan": Plan.STARTER.value,
        "email": "owner@xyz.com",
        "positioning": "ERP para indústria de transformação",
        "campaigns": [("ERP Indústria", "erp-industria", ["BR"], "Diretor Industrial")],
        "doc": "Casos de implantação de ERP em indústrias de autopeças.",
    },
]

SENHA_DEMO = "demo-senha-12345"


def main() -> int:
    for demo in DEMOS:
        with unscoped_session(reason="seed:create-tenant") as session:
            if session.execute(
                select(Tenant).where(Tenant.slug == demo["slug"])
            ).scalar_one_or_none():
                print(f"· {demo['tenant']}: já existe, pulando")
                continue
            tenant = Tenant(name=demo["tenant"], slug=demo["slug"], plan=demo["plan"])
            user = User(
                email=demo["email"],
                password_hash=hash_password(SENHA_DEMO),
                full_name="Owner",
            )
            session.add_all([tenant, user])
            session.flush()
            session.add(Membership(tenant_id=tenant.id, user_id=user.id, role="owner"))
            session.flush()
            tenant_id = tenant.id

        with tenant_session(tenant_id) as session:
            session.add(
                CompanyProfile(
                    tenant_id=tenant_id,
                    legal_name=demo["tenant"],
                    positioning=demo["positioning"],
                    brand_voice={"tom": "direto", "idioma": "pt-BR"},
                )
            )
            for nome, slug, geo, persona in demo["campaigns"]:
                session.add(
                    Campaign(
                        tenant_id=tenant_id,
                        name=nome,
                        slug=slug,
                        status="active",
                        target_geography=geo,
                        personas=[{"cargo": persona}],
                        icp={"persona": persona, "geografia": geo},
                        channels=["email"],
                        daily_limits={"emails": 50},
                    )
                )
            doc = KnowledgeDocument(
                tenant_id=tenant_id, title=f"Base {demo['tenant']}", status="indexed"
            )
            session.add(doc)
            session.flush()
            session.add(
                KnowledgeChunk(
                    tenant_id=tenant_id, document_id=doc.id, ordinal=0, content=demo["doc"]
                )
            )
        if demo.get("rascunho"):
            _seed_fila_de_revisao(tenant_id)
        print(f"✓ {demo['tenant']} criado — login {demo['email']} / {SENHA_DEMO}")
    return 0


def _seed_fila_de_revisao(tenant_id) -> None:
    """Um prospect com rascunho, para a fila de revisão não nascer vazia.

    Serve para ver a tela funcionando sem chave de API: o texto abaixo foi
    escrito à mão, não por um modelo.
    """
    with tenant_session(tenant_id) as session:
        campanha = session.execute(select(Campaign)).scalars().first()
        empresa = Company(
            tenant_id=tenant_id, name="Northern Ore", domain="northernore.ca", country="CA"
        )
        session.add(empresa)
        session.flush()
        contato = Contact(
            tenant_id=tenant_id,
            company_id=empresa.id,
            full_name="Alice Tremblay",
            email="alice@northernore.ca",
            title="CFO",
        )
        session.add(contato)
        session.flush()
        prospect = Prospect(
            tenant_id=tenant_id,
            campaign_id=campanha.id,
            contact_id=contato.id,
            company_id=empresa.id,
            status="scored",
            source="demo",
        )
        session.add(prospect)
        session.flush()
        conversa = Conversation(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            campaign_id=campanha.id,
            channel="email",
            subject="Turnos em Sudbury",
        )
        session.add(conversa)
        session.flush()
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversa.id,
                direction="outbound",
                status="draft",
                channel="email",
                subject="Turnos em Sudbury",
                body=(
                    "Alice, vi as duas vagas de gerente de operações em Sudbury.\n\n"
                    "Quando uma operação cresce assim, a escala costuma virar planilha "
                    "compartilhada — e o custo aparece no turno extra, não no relatório.\n\n"
                    "Vale 15 minutos?"
                ),
                metrics={
                    "to": "alice@northernore.ca",
                    "anchors": [
                        {
                            "fact": "Duas vagas de gerente de operações em Sudbury",
                            "how_used": "abertura da mensagem",
                        }
                    ],
                },
            )
        )


if __name__ == "__main__":
    sys.exit(main())
