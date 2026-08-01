"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";

export type FocusCardItem = {
  id: string;
  title: string;
  description: string;
  tint: string;
  icon: ReactNode;
  driftClass: string;
};

type FocusCardsProps = {
  cards: FocusCardItem[];
  onSelect: (id: string) => void;
  reducedMotion: boolean;
};

export function FocusCards({
  cards,
  onSelect,
  reducedMotion,
}: FocusCardsProps) {
  const [focused, setFocused] = useState<number | null>(null);

  return (
    <div
      className="focus-bubble-field"
      onMouseLeave={() => setFocused(null)}
      role="list"
      aria-label="Choose a corner"
    >
      {cards.map((card, index) => (
        <motion.button
          layoutId={reducedMotion ? undefined : `corner-${card.id}`}
          key={card.id}
          type="button"
          role="listitem"
          className={cn(
            "soap-bubble",
            !reducedMotion && card.driftClass,
            focused !== null && focused !== index && "bubble-receded",
            focused === index && "bubble-focused",
          )}
          style={{ "--bubble-tint": card.tint } as React.CSSProperties}
          onMouseEnter={() => setFocused(index)}
          onFocus={() => setFocused(index)}
          onBlur={() => setFocused(null)}
          onClick={() => onSelect(card.id)}
          aria-label={`${card.title}. ${card.description}`}
          transition={
            reducedMotion
              ? { duration: 0.15 }
              : { type: "spring", stiffness: 170, damping: 22, mass: 0.8 }
          }
        >
          <span className="bubble-shine" aria-hidden="true" />
          <span className="bubble-icon" aria-hidden="true">
            {card.icon}
          </span>
          <span className="bubble-title">{card.title}</span>
          <span className="bubble-description">{card.description}</span>
        </motion.button>
      ))}
    </div>
  );
}
