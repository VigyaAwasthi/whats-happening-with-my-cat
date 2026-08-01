"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, PawPrint, Settings2 } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import Lenis from "lenis";
import type { CatProfile, Corner } from "@/lib/types";
import { signedMediaUrl } from "@/lib/supabase";

export const CORNER_META = {
  behavior: {
    name: "Chat",
    eyebrow: "Behavior corner",
    color: "#E43D12",
  },
  health: {
    name: "Health",
    eyebrow: "Health corner",
    color: "#D6536D",
  },
  "fun-facts": {
    name: "Fun facts",
    eyebrow: "Curiosity corner",
    color: "#EFB11D",
  },
  "special-moments": {
    name: "Moments",
    eyebrow: "A private scrapbook",
    color: "#FFA2B6",
  },
} satisfies Record<Corner, { name: string; eyebrow: string; color: string }>;

export function useGentleScroll(enabled = true) {
  const reducedMotion = useReducedMotion();
  useEffect(() => {
    if (!enabled || reducedMotion) return;
    const lenis = new Lenis({ autoRaf: true, duration: 1.05 });
    return () => lenis.destroy();
  }, [enabled, reducedMotion]);
}

export function useSignedUrls(keys: string[]) {
  const stableKey = keys.join("|");
  const [urls, setUrls] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    const requested = stableKey ? stableKey.split("|") : [];
    Promise.all(
      requested.map(async (key) => [key, await signedMediaUrl(key)] as const),
    ).then((entries) => {
      if (cancelled) return;
      setUrls(
        Object.fromEntries(
          entries.filter(
            (entry): entry is readonly [string, string] => Boolean(entry[1]),
          ),
        ),
      );
    });
    return () => {
      cancelled = true;
    };
  }, [stableKey]);

  return urls;
}

type CatSelectorProps = {
  cats: CatProfile[];
  activeCat: CatProfile;
  onChange: (catId: string) => void;
  onManage: () => void;
  dark?: boolean;
};

export function CatSelector({
  cats,
  activeCat,
  onChange,
  onManage,
  dark = false,
}: CatSelectorProps) {
  return (
    <div className={`cat-selector ${dark ? "cat-selector-dark" : ""}`}>
      <label>
        <span className="sr-only">Active cat</span>
        <PawPrint size={16} aria-hidden="true" />
        <select
          value={activeCat.id}
          onChange={(event) => onChange(event.target.value)}
          aria-label="Active cat"
        >
          {cats.map((cat) => (
            <option value={cat.id} key={cat.id}>
              {cat.name}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="icon-button" onClick={onManage}>
        <Settings2 size={18} aria-hidden="true" />
        <span className="sr-only">Manage cats and account</span>
      </button>
    </div>
  );
}

type CornerHeaderProps = CatSelectorProps & {
  corner: Corner;
  onBack: () => void;
};

export function CornerHeader({
  corner,
  onBack,
  ...selectorProps
}: CornerHeaderProps) {
  const meta = CORNER_META[corner];
  return (
    <header className="corner-header">
      <button type="button" className="back-button" onClick={onBack}>
        <ArrowLeft size={18} aria-hidden="true" />
        <span>Back to the wall</span>
      </button>
      <div className="corner-title-lockup">
        <span>{meta.eyebrow}</span>
        <strong>{meta.name}</strong>
      </div>
      <CatSelector {...selectorProps} />
    </header>
  );
}

export function LoadingMark({ label = "One moment" }: { label?: string }) {
  const reducedMotion = useReducedMotion();
  return (
    <div className="loading-mark" role="status">
      <motion.span
        animate={reducedMotion ? undefined : { rotate: [0, 12, -8, 0] }}
        transition={{ repeat: Infinity, duration: 1.8, ease: "easeInOut" }}
      >
        <PawPrint size={18} aria-hidden="true" />
      </motion.span>
      {label}
    </div>
  );
}

export function EmptyPhoto({
  cat,
  className = "",
}: {
  cat: CatProfile;
  className?: string;
}) {
  const initials = useMemo(
    () =>
      cat.name
        .split(/\s+/)
        .map((part) => part[0])
        .join("")
        .slice(0, 2)
        .toUpperCase(),
    [cat.name],
  );
  return (
    <div
      className={`empty-photo ${className}`}
      style={{ "--cat-accent": cat.theme.primary_color } as React.CSSProperties}
      aria-label={`${cat.name}'s photo placeholder`}
    >
      <span>{initials}</span>
      <PawPrint aria-hidden="true" />
    </div>
  );
}

export function FeedbackButtons({
  onFeedback,
}: {
  onFeedback: (thumb: "up" | "down") => void;
}) {
  const [sent, setSent] = useState<"up" | "down" | null>(null);
  return (
    <div className="feedback-row" aria-label="Was this helpful?">
      {sent ? (
        <span>Thank you for telling us.</span>
      ) : (
        <>
          <span>Helpful?</span>
          <button
            type="button"
            onClick={() => {
              setSent("up");
              onFeedback("up");
            }}
          >
            Yes
          </button>
          <button
            type="button"
            onClick={() => {
              setSent("down");
              onFeedback("down");
            }}
          >
            Not quite
          </button>
        </>
      )}
    </div>
  );
}
