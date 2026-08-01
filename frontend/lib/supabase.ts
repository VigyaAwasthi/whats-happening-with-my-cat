"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { AuthSession } from "@/lib/types";

let client: SupabaseClient | null | undefined;
let storageSessionReady = false;

export function getSupabase(): SupabaseClient | null {
  if (client !== undefined) return client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  client = url && anonKey ? createClient(url, anonKey) : null;
  return client;
}

export async function attachSupabaseSession(session: AuthSession): Promise<void> {
  const supabase = getSupabase();
  storageSessionReady = false;
  if (!supabase || session.access_token.startsWith("development-")) return;
  const { error } = await supabase.auth.setSession({
    access_token: session.access_token,
    refresh_token: session.refresh_token,
  });
  if (error) throw error;
  storageSessionReady = true;
}

export async function uploadCatMedia(
  catId: string,
  file: File,
): Promise<{ key: string; previewUrl: string }> {
  const supabase = getSupabase();
  if (!supabase || !storageSessionReady) {
    // OPEN QUESTION: the zero-cost backend returns development-only auth tokens,
    // which cannot authorize Supabase Storage. Keep a local preview and persist
    // no fake storage key; production auth uses the real upload path below.
    return { key: "", previewUrl: URL.createObjectURL(file) };
  }

  const safeName = file.name
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const key = `${catId}/${crypto.randomUUID()}-${safeName || "memory"}`;
  const { error } = await supabase.storage
    .from("cat-media")
    .upload(key, file, { cacheControl: "3600", upsert: false });
  if (error) throw error;
  return { key, previewUrl: URL.createObjectURL(file) };
}

export async function signedMediaUrl(key: string): Promise<string | null> {
  if (!key) return null;
  const supabase = getSupabase();
  if (!supabase || !storageSessionReady) return null;
  const { data, error } = await supabase.storage
    .from("cat-media")
    .createSignedUrl(key, 60 * 60);
  return error ? null : data.signedUrl;
}

export async function deleteCatMedia(key: string): Promise<void> {
  if (!key) return;
  const supabase = getSupabase();
  if (!supabase || !storageSessionReady) return;
  const { error } = await supabase.storage.from("cat-media").remove([key]);
  if (error) throw error;
}
