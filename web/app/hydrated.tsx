"use client";

import { useEffect, useState } from "react";

/**
 * Marca no DOM que o React assumiu a página.
 *
 * Existe por causa de uma falha de verdade no CI: a fumaça esperava por um
 * elemento renderizado no servidor e clicava logo depois. Numa máquina rápida a
 * hidratação já tinha terminado; num runner frio, não — e o clique era
 * engolido, o que aparecia como falha intermitente e inexplicável.
 *
 * Não muda nada visualmente. Serve ao teste e a quem depura: dá como saber que
 * os formulários da tela já respondem, em vez de supor. Como não é visível, o
 * teste espera por ela com `state: "attached"`.
 */
export default function MarcaDeHidratacao() {
  const [hidratado, setHidratado] = useState(false);
  useEffect(() => setHidratado(true), []);
  if (!hidratado) return null;
  return <span data-hidratado="1" hidden />;
}
