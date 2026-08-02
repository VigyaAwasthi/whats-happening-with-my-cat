"use client";

import {
  ArrowUp,
  ExternalLink,
  HeartPulse,
  Quote,
  Sparkle,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useRef, useState, type FormEvent } from "react";
import { catApi } from "@/lib/api";
import { safeExternalUrl } from "@/lib/url";
import type { BehaviorMessage, CatProfile } from "@/lib/types";
import {
  CornerHeader,
  FeedbackButtons,
  LoadingMark,
} from "@/components/cat-app/shared";

type ChatCornerProps = {
  token: string;
  cats: CatProfile[];
  activeCat: CatProfile;
  sessionId: string;
  messages: BehaviorMessage[];
  onSessionId: (sessionId: string) => void;
  onMessages: (messages: BehaviorMessage[]) => void;
  onSwitchCat: (catId: string) => void;
  onManage: () => void;
  onBack: () => void;
  onHealthNudge: () => void;
};

export function ChatCorner({
  token,
  cats,
  activeCat,
  sessionId,
  messages,
  onSessionId,
  onMessages,
  onSwitchCat,
  onManage,
  onBack,
  onHealthNudge,
}: ChatCornerProps) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const reducedMotion = Boolean(useReducedMotion());

  async function send(event: FormEvent) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || busy) return;
    const userMessage: BehaviorMessage = {
      id: crypto.randomUUID(),
      role: "user",
      text: message,
    };
    const nextMessages = [...messages, userMessage];
    onMessages(nextMessages);
    setDraft("");
    setError("");
    setBusy(true);
    try {
      const response = await catApi.behavior(
        token,
        activeCat.id,
        message,
        sessionId,
      );
      onSessionId(response.session_id);
      onMessages([
        ...nextMessages,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: response.result.interpretation,
          generation_id: response.generation_id,
          result: response.result,
        },
      ]);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The chat corner is quiet for a moment. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.main
      layoutId={reducedMotion ? undefined : "corner-behavior"}
      className="corner-room chat-room"
      transition={{ type: "spring", stiffness: 150, damping: 24, mass: 0.9 }}
    >
      <CornerHeader
        corner="behavior"
        cats={cats}
        activeCat={activeCat}
        onChange={onSwitchCat}
        onManage={onManage}
        onBack={onBack}
      />

      <div className="chat-layout">
        <section className="chat-intro">
          <p className="eyebrow">A conversation about {activeCat.name}</p>
          <h1>
            Tell me what
            <br />
            <em>they&apos;re doing.</em>
          </h1>
          <p>
            Behavior is context, not certainty. I&apos;ll separate what is
            well-established from what may simply be {activeCat.name}&apos;s way.
          </p>
          <div className="cat-context-note">
            <Sparkle size={17} aria-hidden="true" />
            <span>
              Remembering: {activeCat.age.value} {activeCat.age.unit},{" "}
              {activeCat.breed ?? "breed unknown"}, energy {activeCat.energy_level}/5
            </span>
          </div>
        </section>

        <section className="conversation-panel" aria-live="polite">
          <div className="message-list">
            {messages.length === 0 ? (
              <div className="chat-empty">
                <Quote size={24} aria-hidden="true" />
                <p>
                  “Why does {activeCat.name} stare at the hallway?” is a perfectly
                  good place to begin.
                </p>
              </div>
            ) : (
              messages.map((message) =>
                message.role === "user" ? (
                  <article className="message message-user" key={message.id}>
                    <span>You</span>
                    <p>{message.text}</p>
                  </article>
                ) : (
                  <article className="message message-assistant" key={message.id}>
                    <header>
                      <span>For {activeCat.name}</span>
                      <span
                        className={`confidence confidence-${message.result.confidence}`}
                      >
                        {message.result.confidence === "varies-by-cat"
                          ? "Varies by cat"
                          : message.result.confidence}
                      </span>
                    </header>
                    <p className="interpretation">{message.text}</p>
                    <details>
                      <summary>Why this fits</summary>
                      <p>{message.result.reasoning}</p>
                    </details>
                    {message.result.answer_mode === "corpus_grounded" ? (
                      message.result.cited_entries.length ? (
                        <div className="behavior-sources" aria-label="Sources">
                          <span>From a trusted source</span>
                          {message.result.cited_entries.map((citation) =>
                            safeExternalUrl(citation.url) ? (
                              <a
                                key={citation.entry_id}
                                href={safeExternalUrl(citation.url) ?? undefined}
                                target="_blank"
                                rel="noreferrer"
                                aria-label={`${citation.title}, ${citation.organization} — opens in a new tab`}
                              >
                                <span>
                                  <strong>{citation.title}</strong>
                                  <small>{citation.organization}</small>
                                </span>
                                <ExternalLink size={13} aria-hidden="true" />
                              </a>
                            ) : (
                              <span
                                className="behavior-source-text"
                                key={citation.entry_id}
                              >
                                <strong>{citation.title}</strong>
                                <small>{citation.organization}</small>
                              </span>
                            ),
                          )}
                        </div>
                      ) : null
                    ) : (
                      <p className="behavior-origin">
                        General feline understanding
                      </p>
                    )}
                    {message.result.suggested_clarifying_questions.length ? (
                      <div className="clarifying-block">
                        <span>A useful next detail</span>
                        <div>
                          {message.result.suggested_clarifying_questions.map(
                            (question) => (
                              <button
                                type="button"
                                key={question}
                                onClick={() => {
                                  setDraft(question);
                                  inputRef.current?.focus();
                                }}
                              >
                                {question}
                              </button>
                            ),
                          )}
                        </div>
                      </div>
                    ) : null}
                    {message.result.medical_nudge ? (
                      <button
                        type="button"
                        className="medical-nudge"
                        onClick={onHealthNudge}
                      >
                        <HeartPulse size={18} aria-hidden="true" />
                        <span>
                          This may be medical. Move to the health corner
                          <small>One tap · same active cat</small>
                        </span>
                      </button>
                    ) : null}
                    <FeedbackButtons
                      onFeedback={(thumb) => {
                        void catApi.feedback(token, {
                          cat_id: activeCat.id,
                          session_id: sessionId,
                          corner: "behavior",
                          thumb,
                          generation_id: message.generation_id,
                        });
                      }}
                    />
                  </article>
                ),
              )
            )}
            {busy ? <LoadingMark label={`Thinking about ${activeCat.name}…`} /> : null}
          </div>

          <form className="chat-composer" onSubmit={send}>
            <label htmlFor="behavior-message" className="sr-only">
              Ask about {activeCat.name}&apos;s behavior
            </label>
            <textarea
              ref={inputRef}
              id="behavior-message"
              rows={2}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder={`What has ${activeCat.name} been up to?`}
            />
            <button type="submit" disabled={busy || !draft.trim()}>
              <ArrowUp size={18} aria-hidden="true" />
              <span className="sr-only">Send message</span>
            </button>
          </form>
          {error ? (
            <p className="form-error composer-error" role="alert">
              {error}
            </p>
          ) : null}
        </section>
      </div>
    </motion.main>
  );
}
