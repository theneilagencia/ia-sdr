import Aceitar from "./aceitar";

/**
 * A página pública do convite.
 *
 * Pública de propósito: quem clica no link ainda não tem sessão, e mandá-la para
 * o login antes seria pedir a senha de uma conta que talvez não exista.
 *
 * A tela não sabe — nem tenta descobrir — se o email já tem conta na plataforma.
 * Validar o token aqui exigiria uma rota que respondesse "este convite é válido
 * e é para tal email", e essa rota é um oráculo aberto na internet: com ela,
 * qualquer pessoa com o link descobre para quem ele foi mandado. Então o campo
 * de senha serve aos dois casos com a mesma frase, e a única resposta vem do
 * aceite.
 */
export default async function ConvitePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  return (
    <main className="shell login">
      <h1>Aceitar convite</h1>
      <p className="lede">
        Você foi convidado a trabalhar numa empresa na plataforma. Defina sua senha para
        entrar — ou, se já tem conta aqui, use a senha que você já usa.
      </p>
      <Aceitar token={token} />
    </main>
  );
}
