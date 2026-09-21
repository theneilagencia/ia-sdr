import { cookies } from "next/headers";

/**
 * O token vive num cookie httpOnly.
 *
 * Guardar JWT em localStorage é o padrão mais comum e o mais fácil de roubar:
 * qualquer script na página lê. Como aqui só o servidor do Next precisa do
 * token, o cookie httpOnly é mais simples e não fica exposto ao browser.
 */
export const SESSION_COOKIE = "ia_sdr_session";

export async function getToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

export async function setToken(token: string, ttlMinutes: number) {
  const store = await cookies();
  store.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: ttlMinutes * 60,
  });
}

export async function clearToken() {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
}
