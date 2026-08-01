"use client";

import { HeartPulse, Images, MessageCircle, Sparkles } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import {
  FocusCards,
  type FocusCardItem,
} from "@/components/ui/focus-cards";
import {
  CatSelector,
  EmptyPhoto,
  useSignedUrls,
} from "@/components/cat-app/shared";
import type { CatProfile, Corner } from "@/lib/types";

const BUBBLES: FocusCardItem[] = [
  {
    id: "behavior",
    title: "Chat",
    description: "What might they be telling you?",
    tint: "#E43D12",
    icon: <MessageCircle />,
    driftClass: "bubble-drift-one",
  },
  {
    id: "health",
    title: "Health",
    description: "Careful guidance from trusted sources.",
    tint: "#D6536D",
    icon: <HeartPulse />,
    driftClass: "bubble-drift-two",
  },
  {
    id: "fun-facts",
    title: "Fun facts",
    description: "Small wonders, picked for this cat.",
    tint: "#EFB11D",
    icon: <Sparkles />,
    driftClass: "bubble-drift-three",
  },
  {
    id: "special-moments",
    title: "Moments",
    description: "A private wall for what matters.",
    tint: "#FFA2B6",
    icon: <Images />,
    driftClass: "bubble-drift-four",
  },
];

type HubProps = {
  cats: CatProfile[];
  activeCat: CatProfile;
  onSwitchCat: (catId: string) => void;
  onManage: () => void;
  onEnterCorner: (corner: Corner) => void;
};

export function Hub({
  cats,
  activeCat,
  onSwitchCat,
  onManage,
  onEnterCorner,
}: HubProps) {
  const reducedMotion = Boolean(useReducedMotion());

  return (
    <motion.main
      className="hub-screen"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reducedMotion ? 0.15 : 0.4 }}
      style={{ "--cat-accent": activeCat.theme.primary_color } as React.CSSProperties}
    >
      <header className="hub-header">
        <div className="brand-wordmark">Whisker rooms</div>
        <p>
          <span>Home for</span> {activeCat.name}
        </p>
        <CatSelector
          cats={cats}
          activeCat={activeCat}
          onChange={onSwitchCat}
          onManage={onManage}
        />
      </header>

      <section className="photo-wall" aria-label={`${activeCat.name}'s photo wall`}>
        <div className="fairy-wire" aria-hidden="true">
          {Array.from({ length: 11 }, (_, index) => (
            <span key={index} />
          ))}
        </div>
        <PhotoString cat={activeCat} />
      </section>

      <section className="hub-copy">
        <p className="eyebrow">Four corners, one very particular cat</p>
        <h1>
          Where would you like
          <br />
          to spend a little time?
        </h1>
      </section>

      <FocusCards
        cards={BUBBLES}
        reducedMotion={reducedMotion}
        onSelect={(id) => onEnterCorner(id as Corner)}
      />

      <p className="hub-boundary">
        Health guidance is informational and never a substitute for a veterinarian.
      </p>
    </motion.main>
  );
}

function PhotoString({ cat }: { cat: CatProfile }) {
  const references = cat.photo_references.slice(0, 5);
  const signed = useSignedUrls(references);
  const slots = references.length ? references : ["", "", ""];

  return (
    <div className="polaroid-string">
      {slots.map((reference, index) => {
        const photo = reference ? signed[reference] : null;
        return (
          <motion.figure
            key={reference || index}
            className={`polaroid polaroid-${(index % 5) + 1}`}
            animate={{ rotate: [0, index % 2 ? 0.7 : -0.7, 0] }}
            transition={{
              repeat: Infinity,
              duration: 6 + index * 0.8,
              ease: "easeInOut",
            }}
          >
            <span className="photo-peg" aria-hidden="true" />
            {photo ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={photo} alt={`${cat.name}, photo ${index + 1}`} />
            ) : (
              <EmptyPhoto cat={cat} />
            )}
            <figcaption>
              {index === 0 ? cat.name : ["today", "small joy", "home"][index % 3]}
            </figcaption>
          </motion.figure>
        );
      })}
    </div>
  );
}
