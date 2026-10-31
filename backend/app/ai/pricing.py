"""Custo por token, por modelo.

Uma plataforma que revende IA precisa saber a margem de cada operação. As
unidades de consumo (`app/services/usage.py`) são a moeda interna, cobrada do
cliente; isto aqui é o custo real, em dólar, do que foi gasto.

Conveniência aritmética que vale conhecer: preço em dólares por milhão de
tokens é numericamente igual a **micro-dólares por token** — US$ 5,00/MTok são
5 micro-USD por token. Por isso a tabela abaixo serve para os dois usos sem
conversão, e o custo fica em inteiro, sem float em dinheiro.

Preços de tabela da API da Anthropic (primeira parte, conferidos em
2026-06-24). Bedrock e Vertex têm preços próprios. Revise quando mudar:
https://www.anthropic.com/pricing
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


PRICES: dict[str, ModelPrice] = {
    "claude-opus-5": ModelPrice(5.00, 25.00),
    "claude-opus-4-8": ModelPrice(5.00, 25.00),
    "claude-sonnet-5": ModelPrice(2.00, 10.00),
    "claude-haiku-4-5": ModelPrice(1.00, 5.00),
    "claude-fable-5-1": ModelPrice(10.00, 50.00),
}

#: Modelo desconhecido não pode virar custo zero — isso esconderia gasto real.
#: Usa-se o preço mais alto da tabela, que superestima em vez de sumir.
_FALLBACK = ModelPrice(10.00, 50.00)


def price_for(model: str) -> ModelPrice:
    return PRICES.get(model, _FALLBACK)


def cost_micro_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> int:
    """Custo em micro-dólares (milionésimos de dólar).

    Tokens de cache entram pelo preço de entrada. Leitura de cache custa menos
    do que isso na prática, então o número superestima — de propósito: para
    controle de margem, errar para cima é seguro, errar para baixo não.
    """
    price = price_for(model)
    billable_input = input_tokens + cache_read_tokens + cache_write_tokens
    total = billable_input * price.input_per_mtok + output_tokens * price.output_per_mtok
    return int(round(total))


def usage_to_cost(model: str, usage) -> int:
    """Extrai os tokens de um objeto `usage` do SDK e devolve o custo."""
    return cost_micro_usd(
        model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )
