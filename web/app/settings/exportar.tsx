/**
 * Levar os dados embora.
 *
 * A API já devolvia o pacote inteiro; só não havia por onde pedir sem `curl` —
 * e "dá para ir embora" que exige linha de comando, para quem avalia entrar, é
 * o mesmo que não existir. O download passa pelo servidor do Next porque o
 * token vive em cookie httpOnly e não pode chegar ao browser.
 */
export default function Exportar({ papel }: { papel: string }) {
  const pode = papel === "owner" || papel === "admin";

  return (
    <section className="card">
      <h3>Levar os dados embora</h3>
      <p className="ajuda">
        Um JSON com tudo desta empresa: contas, contatos, prospects, conversas,
        mensagens, pesquisa, qualificações, cadências e consumo. Sem nenhum segredo
        junto — chave de IA, senha de email e token do CRM não são serializados.
      </p>
      {pode ? (
        <p>
          <a className="botao" href="/exportar" download>
            Baixar exportação
          </a>
        </p>
      ) : (
        <p className="ajuda">
          Só quem administra a empresa pode exportar. Peça a um owner ou admin.
        </p>
      )}
    </section>
  );
}
