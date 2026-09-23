/**
 * Dinheiro formatado, de um lado e do outro da fronteira.
 *
 * Mora num módulo próprio por um motivo concreto: a primeira versão exportava
 * este formatador do módulo de formulários, que é `"use client"`, e a tabela de
 * faturas — componente de servidor — quebrava em execução com "não é possível
 * invocar uma função de cliente a partir do servidor". Compilava e passava no
 * typecheck; só a fumaça pegou.
 */

/** Centavos inteiros na moeda do contrato. Dinheiro não anda em float. */
export function emCentavos(centavos: number, moeda: string): string {
  return (centavos / 100).toLocaleString("pt-BR", { style: "currency", currency: moeda });
}

/** O valor para o campo do formulário, na moeda: zero vira vazio, não "0,00". */
export function paraOCampo(centavos: number): string {
  return centavos ? (centavos / 100).toFixed(2) : "";
}
