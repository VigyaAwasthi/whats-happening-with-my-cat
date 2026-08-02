"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { prepareImageForUpload } from "@/lib/images";
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
  // Convert BEFORE anything else, including before the development-mode early
  // return. Conversion is what proves the image can be rendered, so a file that
  // fails here must never reach storage or a preview — that was the original
  // bug, where an unrenderable HEIC uploaded "successfully".
  const prepared = await prepareImageForUpload(file);

  const supabase = getSupabase();
  if (!supabase || !storageSessionReady) {
    // The zero-cost backend returns development-only auth tokens, which cannot
    // authorize Supabase Storage. Keep a local preview and persist no fake
    // storage key; production auth uses the real upload path below.
    return { key: "", previewUrl: URL.createObjectURL(prepared.blob) };
  }

  const baseName = file.name
    .replace(/\.[^.]+$/, "")
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  // The stored extension is the converted one. Keeping the original `.heic`
  // here would make the object undisplayable again the moment anything served
  // it by extension.
  const key = `${catId}/${crypto.randomUUID()}-${baseName || "memory"}.${prepared.extension}`;
  const { error } = await supabase.storage
    .from("cat-media")
    .upload(key, prepared.blob, {
      cacheControl: "3600",
      upsert: false,
      contentType: prepared.blob.type,
    });
  if (error) throw error;
  return { key, previewUrl: URL.createObjectURL(prepared.blob) };
}

/**
 * Upload a blob that `prepareImageForUpload` has already decoded and
 * re-encoded. The editor converts at pick time so the preview proves the image
 * renders, which means by save time there is nothing left to convert.
 */
export async function uploadPreparedCatMedia(
  catId: string,
  blob: Blob,
  originalName: string,
  extension: "webp" | "jpg",
): Promise<{ key: string }> {
  const supabase = getSupabase();
  if (!supabase || !storageSessionReady) return { key: "" };

  const baseName = originalName
    .replace(/\.[^.]+$/, "")
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const key = `${catId}/${crypto.randomUUID()}-${baseName || "memory"}.${extension}`;
  const { error } = await supabase.storage
    .from("cat-media")
    .upload(key, blob, {
      cacheControl: "3600",
      upsert: false,
      contentType: blob.type,
    });
  if (error) throw error;
  return { key };
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
