import Link from "next/link";

/**
 * A página que não existe — em português, com saída.
 *
 * O padrão do Next é um "404 | This page could not be found" em inglês, sem
 * link nenhum. Numa plataforma cujos endereços têm uuid no meio, chegar aqui é
 * rotina: link salvo depois de o registro sair, id colado pela metade, endereço
 * repassado por outra pessoa.
 */
export default function NaoEncontrado() {
  return (
    <main className="shell login">
      <h1>Não encontramos esta página</h1>
      <p className="lede">
        O endereço pode estar incompleto, ou o registro que ele abria já não existe
        nesta empresa. Nada foi perdido por você ter chegado aqui.
      </p>
      <p>
        <Link className="botao" href="/">
          Voltar ao funil
        </Link>
      </p>
    </main>
  );
}
