/** Return a safe absolute HTTP(S) link, or null so callers render plain text. */
export function safeExternalUrl(
  value: string | null | undefined,
): string | null {
  if (!value || value.trim() !== value || /\s/.test(value)) return null;
  const lowered = value.toLowerCase();
  if (
    lowered.includes("verify") ||
    lowered.includes("placeholder") ||
    lowered.includes("[") ||
    lowered.includes("]") ||
    lowered.includes("%5bverify")
  ) {
    return null;
  }
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? value
      : null;
  } catch {
    return null;
  }
}
