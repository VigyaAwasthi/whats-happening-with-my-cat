"use client";

import { ArrowUp, Check, Clock3, ExternalLink } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useState, type FormEvent } from "react";
import { catApi } from "@/lib/api";
import { safeExternalUrl } from "@/lib/url";
import type {
  BodySystem,
  CatProfile,
  HealthExchange,
  SymptomIntake,
  TriageResult,
} from "@/lib/types";
import {
  CornerHeader,
  FeedbackButtons,
  LoadingMark,
} from "@/components/cat-app/shared";

type Concern = {
  id: string;
  label: string;
  phrase: string;
  systems: BodySystem[];
  intake: Partial<SymptomIntake>;
};

const CONCERNS: Concern[] = [
  {
    id: "not-eating",
    label: "Not eating",
    phrase: "My cat is not eating.",
    systems: ["digestive", "systemic"],
    intake: { appetite_change: "not-eating" },
  },
  {
    id: "vomiting",
    label: "Vomiting",
    phrase: "My cat has vomited.",
    systems: ["digestive"],
    intake: { vomiting: "once" },
  },
  {
    id: "litter-box",
    label: "Litter box change",
    phrase: "There is a litter box change.",
    systems: ["urinary"],
    intake: { litter_box_change: true },
  },
  {
    id: "lethargy",
    label: "Lethargic",
    phrase: "My cat is lethargic.",
    systems: ["systemic"],
    intake: { lethargy: true },
  },
  {
    id: "breathing",
    label: "Breathing change",
    phrase: "My cat is breathing differently.",
    systems: ["respiratory"],
    intake: { breathing_change: true },
  },
  {
    id: "eyes",
    label: "Eye concern",
    phrase: "I am worried about my cat's eye.",
    systems: ["eyes"],
    intake: {},
  },
  {
    id: "mobility",
    label: "Walking or mobility",
    phrase: "My cat has a walking or mobility problem.",
    systems: ["musculoskeletal"],
    intake: {},
  },
  {
    id: "toxin",
    label: "May have eaten something",
    phrase: "There may have been a toxin ingestion.",
    systems: ["toxin"],
    intake: {},
  },
];

type HealthCornerProps = {
  token: string;
  cats: CatProfile[];
  activeCat: CatProfile;
  sessionId: string;
  exchanges: HealthExchange[];
  onSessionId: (sessionId: string) => void;
  onExchanges: (exchanges: HealthExchange[]) => void;
  onSwitchCat: (catId: string) => void;
  onManage: () => void;
  onBack: () => void;
};

export function HealthCorner({
  token,
  cats,
  activeCat,
  sessionId,
  exchanges,
  onSessionId,
  onExchanges,
  onSwitchCat,
  onManage,
  onBack,
}: HealthCornerProps) {
  const [selected, setSelected] = useState<string[]>([]);
  const [details, setDetails] = useState("");
  const [duration, setDuration] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reducedMotion = Boolean(useReducedMotion());

  function toggleConcern(id: string) {
    setSelected((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : [...current, id],
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected.length && !details.trim()) {
      setError("Choose a concern or describe what you are seeing.");
      return;
    }
    setError("");
    setBusy(true);
    const concerns = CONCERNS.filter((item) => selected.includes(item.id));
    const merged: SymptomIntake = {
      body_systems: Array.from(
        new Set(concerns.flatMap((concern) => concern.systems)),
      ),
      duration_hours: duration ? Math.max(0, Number(duration)) : null,
      appetite_change:
        concerns.find((item) => item.intake.appetite_change)?.intake
          .appetite_change ?? "unknown",
      vomiting:
        concerns.find((item) => item.intake.vomiting)?.intake.vomiting ??
        "unknown",
      litter_box_change: concerns.some(
        (item) => item.intake.litter_box_change === true,
      )
        ? true
        : null,
      breathing_change: concerns.some(
        (item) => item.intake.breathing_change === true,
      )
        ? true
        : null,
      lethargy: concerns.some((item) => item.intake.lethargy === true)
        ? true
        : null,
      free_text_residual: details.trim(),
    };
    const rawMessage = [
      ...concerns.map((concern) => concern.phrase),
      details.trim(),
    ]
      .filter(Boolean)
      .join(" ");
    try {
      const response = await catApi.health(
        token,
        activeCat.id,
        sessionId,
        rawMessage || null,
        merged,
      );
      onSessionId(response.session_id);
      onExchanges([
        ...exchanges,
        {
          id: crypto.randomUUID(),
          concern:
            details.trim() ||
            concerns.map((concern) => concern.label).join(", "),
          generation_id: response.generation_id,
          result: response.result,
        },
      ]);
      setSelected([]);
      setDetails("");
      setDuration("");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "We could not check the trusted sources just now.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.main
      layoutId={reducedMotion ? undefined : "corner-health"}
      className="corner-room health-room"
      transition={{ type: "spring", stiffness: 150, damping: 24, mass: 0.9 }}
    >
      <CornerHeader
        corner="health"
        cats={cats}
        activeCat={activeCat}
        onChange={onSwitchCat}
        onManage={onManage}
        onBack={onBack}
      />

      <div className="health-boundary" role="note">
        If your cat is straining to urinate, having trouble breathing, or has
        eaten something toxic, go to a veterinarian now.
      </div>

      <div className="health-layout">
        <section className="health-intake">
          <p className="eyebrow">Start with what you can see</p>
          <h1>
            What is worrying
            <br />
            you about {activeCat.name}?
          </h1>
          <p>
            Choose everything that applies. These details help us look in the
            right trusted sources; they do not create a diagnosis.
          </p>
          <form onSubmit={submit}>
            <fieldset className="concern-field">
              <legend>Common concerns</legend>
              <div>
                {CONCERNS.map((concern) => {
                  const active = selected.includes(concern.id);
                  return (
                    <button
                      type="button"
                      key={concern.id}
                      className={active ? "active" : ""}
                      aria-pressed={active}
                      onClick={() => toggleConcern(concern.id)}
                    >
                      {active ? <Check size={15} aria-hidden="true" /> : null}
                      {concern.label}
                    </button>
                  );
                })}
              </div>
            </fieldset>
            <label className="duration-field">
              <span>
                <Clock3 size={17} aria-hidden="true" />
                How many hours has this been happening?
                <small>optional</small>
              </span>
              <input
                type="number"
                min="0"
                value={duration}
                onChange={(event) => setDuration(event.target.value)}
                placeholder="e.g. 12"
              />
            </label>
            <label>
              Anything else you noticed?
              <textarea
                rows={5}
                value={details}
                onChange={(event) => setDetails(event.target.value)}
                placeholder="Use your own words. Include blood, possible ingestion, unusual breathing, or anything that changed suddenly."
              />
            </label>
            {error ? (
              <p className="form-error" role="alert">
                {error}
              </p>
            ) : null}
            <button className="health-submit" type="submit" disabled={busy}>
              {busy ? "Checking trusted sources…" : "Check trusted sources"}
              <ArrowUp size={17} aria-hidden="true" />
            </button>
          </form>
        </section>

        <section className="health-results" aria-live="polite">
          {exchanges.length === 0 && !busy ? (
            <div className="health-empty">
              <span>Trusted sources, carefully bounded.</span>
              <p>
                Your response will appear here with its urgency, citations, and
                a clear next step.
              </p>
            </div>
          ) : null}
          {exchanges.map((exchange) => (
            <article className="health-exchange" key={exchange.id}>
              <p className="health-question">{exchange.concern}</p>
              <HealthResultView result={exchange.result} />
              <FeedbackButtons
                onFeedback={(thumb) => {
                  void catApi.feedback(token, {
                    cat_id: activeCat.id,
                    session_id: sessionId,
                    corner: "health",
                    thumb,
                    generation_id: exchange.generation_id,
                  });
                }}
              />
            </article>
          ))}
          {busy ? <LoadingMark label="Reading the veterinary corpus…" /> : null}
        </section>
      </div>
    </motion.main>
  );
}

function HealthResultView({ result }: { result: TriageResult }) {
  if (result.response_kind === "emergency_canned") {
    return (
      <div className="emergency-block">
        <span>Emergency guidance</span>
        <h2>Go to a veterinarian now.</h2>
        <p>{result.message}</p>
      </div>
    );
  }

  if (result.response_kind === "no_reliable_information") {
    return (
      <div className="no-reliable-block">
        <span>No reliable match</span>
        <h2>We do not have reliable information on this.</h2>
        <p>{result.message}</p>
      </div>
    );
  }

  const citationsAreValid =
    result.claims.length > 0 &&
    result.claims.every(
      (claim) =>
        claim.source_entry_id &&
        claim.source_title &&
        claim.source_organization &&
        result.retrieved_entry_ids.includes(claim.source_entry_id),
    );
  if (!citationsAreValid) {
    return (
      <div className="no-reliable-block">
        <span>Source check did not pass</span>
        <h2>We cannot safely show this answer.</h2>
        <p>Please speak with a veterinarian about what you are seeing.</p>
      </div>
    );
  }

  const sources = Array.from(
    new Map(
      result.claims.map((claim) => [claim.source_entry_id, claim]),
    ).values(),
  );
  const sourceNumberById = new Map(
    sources.map((source, index) => [source.source_entry_id, index + 1]),
  );

  return (
    <div className="triage-block">
      <header>
        <span className={`urgency urgency-${result.severity}`}>
          {result.severity}
        </span>
        <span>From trusted veterinary sources</span>
      </header>
      <p className="triage-message">{result.message}</p>
      <div className="claim-list">
        {result.claims.map((claim, index) => (
          <div className="claim" key={`${claim.source_entry_id}-${index}`}>
            <p>{claim.text}</p>
            <a href={`#source-${claim.source_entry_id}`}>
              [{sourceNumberById.get(claim.source_entry_id)}] {claim.source_title}
            </a>
          </div>
        ))}
      </div>
      <div className="health-sources">
        <h3>Sources</h3>
        {sources.map((claim, index) => (
          <div
            id={`source-${claim.source_entry_id}`}
            key={`${claim.source_entry_id}-source-${index}`}
          >
            <span className="health-source-copy">
              <strong>
                [{index + 1}] {claim.source_title}
              </strong>
              <small>{claim.source_organization}</small>
            </span>
            {safeExternalUrl(claim.source_url) ? (
              <a
                href={safeExternalUrl(claim.source_url) ?? undefined}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open source ${index + 1} in a new tab`}
              >
                Visit source <ExternalLink size={14} aria-hidden="true" />
              </a>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
