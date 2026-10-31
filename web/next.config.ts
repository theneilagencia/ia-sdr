import type { NextConfig } from "next";

const config: NextConfig = {
  // A API fica no backend; o browser nunca fala direto com ela nem guarda
  // token em localStorage — tudo passa por Server Components e Server Actions,
  // com o JWT num cookie httpOnly.
  env: {},

  // `/api/*` é encaminhado para a API por um route handler, em
  // `app/api/[...caminho]/route.ts`, e não por uma reescrita aqui. O motivo está
  // escrito lá: `next build` congela o destino de uma reescrita no manifesto, e
  // numa imagem Docker o build não conhece o endereço interno que a plataforma
  // vai dar ao serviço da API.
};

export default config;
