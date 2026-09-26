import type { NextConfig } from "next";

const config: NextConfig = {
  // A API fica no backend; o browser nunca fala direto com ela nem guarda
  // token em localStorage — tudo passa por Server Components e Server Actions,
  // com o JWT num cookie httpOnly.
  env: {},
};

export default config;
