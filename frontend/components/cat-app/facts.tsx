"use client";

import {
  ArrowUpRight,
  BookOpen,
  Moon,
  Sparkles,
  X,
} from "lucide-react";
import {
  AnimatePresence,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
} from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { catApi } from "@/lib/api";
import { safeExternalUrl } from "@/lib/url";
import type { CatProfile, FunFact, FunFactDetail } from "@/lib/types";
import {
  CornerHeader,
  LoadingMark,
  useGentleScroll,
} from "@/components/cat-app/shared";

type FactsCornerProps = {
  token: string;
  cats: CatProfile[];
  activeCat: CatProfile;
  onSwitchCat: (catId: string) => void;
  onManage: () => void;
  onBack: () => void;
};

export function FactsCorner({
  token,
  cats,
  activeCat,
  onSwitchCat,
  onManage,
  onBack,
}: FactsCornerProps) {
  const [facts, setFacts] = useState<FunFact[]>([]);
  const [expanded, setExpanded] = useState<FunFactDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sleeping, setSleeping] = useState(false);
  const [batting, setBatting] = useState(false);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reducedMotion = Boolean(useReducedMotion());
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);
  const catX = useSpring(pointerX, { stiffness: 90, damping: 18 });
  const catY = useSpring(pointerY, { stiffness: 90, damping: 18 });
  useGentleScroll();

  const tags = useMemo(() => personalizationTags(activeCat), [activeCat]);
  const personalized = useMemo(
    () => new Set(facts.filter((fact) => fact.tags.some((tag) => tags.includes(tag))).map((fact) => fact.id)),
    [facts, tags],
  );

  useEffect(() => {
    let cancelled = false;
    catApi
      .facts(token, activeCat.id, tags)
      .then((items) => {
        if (!cancelled) setFacts(items);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught.message
              : "The fact shelf could not be opened.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeCat.id, tags, token]);

  useEffect(() => {
    if (reducedMotion) return;
    idleTimer.current = setTimeout(() => setSleeping(true), 9000);
    return () => {
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [reducedMotion]);

  function wakeCat() {
    if (reducedMotion) return;
    setSleeping(false);
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => setSleeping(true), 9000);
  }

  async function openFact(fact: FunFact) {
    setBatting(true);
    window.setTimeout(() => setBatting(false), 650);
    try {
      setExpanded(await catApi.getFact(token, activeCat.id, fact.id));
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "That fact would not unfold.",
      );
    }
  }

  return (
    <motion.main
      layoutId={reducedMotion ? undefined : "corner-fun-facts"}
      className="corner-room facts-room"
      transition={{ type: "spring", stiffness: 150, damping: 24, mass: 0.9 }}
      onPointerMove={(event) => {
        wakeCat();
        if (reducedMotion) return;
        const rect = event.currentTarget.getBoundingClientRect();
        pointerX.set(((event.clientX - rect.left) / rect.width - 0.5) * 24);
        pointerY.set(((event.clientY - rect.top) / rect.height - 0.5) * 16);
      }}
    >
      <CornerHeader
        corner="fun-facts"
        cats={cats}
        activeCat={activeCat}
        onChange={onSwitchCat}
        onManage={onManage}
        onBack={onBack}
      />

      <section className="facts-hero">
        <div>
          <p className="eyebrow">Curated for the cat in front of you</p>
          <h1>
            Small things worth
            <br />
            <em>knowing about cats.</em>
          </h1>
        </div>
        <p>
          Browse slowly. Every card is written and sourced ahead of time—nothing
          here is generated on the fly.
        </p>
      </section>

      <div className="facts-content">
        <section className="fact-grid" aria-live="polite">
          {loading ? <LoadingMark label="Choosing a few good facts…" /> : null}
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          {facts.map((fact, index) => (
            <motion.button
              layoutId={reducedMotion ? undefined : `fact-${fact.id}`}
              type="button"
              className={`fact-card fact-card-${(index % 4) + 1}`}
              key={fact.id}
              onMouseEnter={() => setBatting(index % 3 === 0)}
              onMouseLeave={() => setBatting(false)}
              onClick={() => openFact(fact)}
            >
              <span className="fact-number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="fact-category">{fact.category}</span>
              <strong>{fact.fact}</strong>
              <span className="fact-footer">
                {personalized.has(fact.id) ? (
                  <span className="personalized-tag">
                    <Sparkles size={13} aria-hidden="true" />
                    Picked for {activeCat.name}
                  </span>
                ) : (
                  <span>For all cats</span>
                )}
                <ArrowUpRight size={17} aria-hidden="true" />
              </span>
            </motion.button>
          ))}
        </section>

        <CatCompanion
          name={activeCat.name}
          sleeping={sleeping}
          batting={batting}
          x={catX}
          y={catY}
          reducedMotion={reducedMotion}
        />
      </div>

      <AnimatePresence>
        {expanded ? (
          <motion.div
            className="fact-expansion-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setExpanded(null)}
          >
            <motion.article
              layoutId={reducedMotion ? undefined : `fact-${expanded.id}`}
              className="fact-expansion"
              onClick={(event) => event.stopPropagation()}
              transition={{ type: "spring", stiffness: 190, damping: 25 }}
            >
              <button
                type="button"
                className="fact-close"
                onClick={() => setExpanded(null)}
              >
                <X size={18} aria-hidden="true" />
                <span className="sr-only">Close expanded fact</span>
              </button>
              <span className="fact-category">{expanded.category}</span>
              <p className="fact-hook">
                {expanded.personalization_hook.replaceAll(
                  "{name}",
                  activeCat.name,
                )}
              </p>
              <h2>{expanded.fact}</h2>
              <p className="fact-detail">{expanded.detail}</p>
              <footer>
                <span>
                  <BookOpen size={15} aria-hidden="true" />
                  {expanded.source_note}
                </span>
                {safeExternalUrl(expanded.source_url) ? (
                  <a href={safeExternalUrl(expanded.source_url) ?? undefined} target="_blank" rel="noreferrer">
                    Read the source
                    <ArrowUpRight size={15} aria-hidden="true" />
                  </a>
                ) : null}
              </footer>
            </motion.article>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.main>
  );
}

function personalizationTags(cat: CatProfile): string[] {
  const ageMonths =
    cat.age.unit === "years" ? cat.age.value * 12 : cat.age.value;
  const ageTag =
    ageMonths < 12 ? "age:kitten" : ageMonths >= 132 ? "age:senior" : "age:adult";
  return [
    "all-cats",
    ageTag,
    ...(cat.breed
      ? [`breed:${cat.breed.toLowerCase().replace(/\s+/g, "-")}`]
      : []),
  ];
}

function CatCompanion({
  name,
  sleeping,
  batting,
  x,
  y,
  reducedMotion,
}: {
  name: string;
  sleeping: boolean;
  batting: boolean;
  x: ReturnType<typeof useSpring>;
  y: ReturnType<typeof useSpring>;
  reducedMotion: boolean;
}) {
  return (
    <aside className="fact-cat-stage" aria-label={`${name}'s animated fact companion`}>
      <motion.div
        className={`css-cat ${sleeping ? "cat-sleeping" : ""} ${
          batting ? "cat-batting" : ""
        }`}
        style={reducedMotion ? undefined : { x, y }}
      >
        <svg
          viewBox="0 0 180 140"
          role="img"
          aria-label={`${name} browsing the fact cards`}
        >
          <path
            className="cat-tail"
            d="M128 103 C164 98 174 72 157 51"
          />
          <ellipse className="cat-body" cx="116" cy="91" rx="52" ry="37" />
          <g className="cat-head">
            <path className="cat-ear" d="M36 57 L45 21 L64 51 Z" />
            <path className="cat-ear" d="M75 50 L96 21 L99 62 Z" />
            <ellipse className="cat-face" cx="68" cy="78" rx="39" ry="36" />
            <ellipse className="cat-eye" cx="54" cy="76" rx="4" ry="6" />
            <ellipse className="cat-eye" cx="81" cy="76" rx="4" ry="6" />
            <ellipse className="cat-nose" cx="68" cy="89" rx="4" ry="3" />
          </g>
          <rect className="cat-paw" x="61" y="111" width="48" height="18" rx="9" />
        </svg>
      </motion.div>
      <div className="cat-status">
        {sleeping ? <Moon size={14} aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}
        <span>{sleeping ? "Curiosity nap." : `${name} is browsing too.`}</span>
      </div>
    </aside>
  );
}
