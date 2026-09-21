"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { api, ApiError } from "@/lib/api";
import { clearToken, setToken } from "@/lib/session";

type TokenResponse = { access_token: string; expires_in_minutes: number };

export async function login(_: string | null, form: FormData): Promise<string | null> {
  const email = String(form.get("email") ?? "");
  const password = String(form.get("password") ?? "");
  try {
    const token = await api<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
      requireAuth: false,
    });
    await setToken(token.access_token, token.expires_in_minutes);
  } catch (erro) {
    // Mensagem única: não confirmar se o email existe é parte do desenho.
    if (erro instanceof ApiError) return "Email ou senha inválidos";
    throw erro;
  }
  redirect("/");
}

export async function logout() {
  await clearToken();
  redirect("/login");
}

export async function approveDraft(formData: FormData) {
  const id = String(formData.get("id"));
  await api(`/api/v1/messages/${id}/approve`, { method: "POST" });
  revalidatePath("/drafts");
}

export async function rejectDraft(formData: FormData) {
  const id = String(formData.get("id"));
  const reason = String(formData.get("reason") ?? "").trim();
  await api(`/api/v1/messages/${id}/reject`, {
    method: "POST",
    body: { reason: reason || null },
  });
  revalidatePath("/drafts");
}
