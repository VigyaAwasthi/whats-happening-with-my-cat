"use client";

import { useState, type FormEvent } from "react";
import { ArrowRight, Heart, MailCheck, ShieldCheck } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { ApiError, catApi } from "@/lib/api";
import type { AuthSession } from "@/lib/types";

type WelcomeProps = {
  onAuthenticated: (session: AuthSession) => Promise<void>;
};

export function Welcome({ onAuthenticated }: WelcomeProps) {
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-up");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Set once sign-up returns `confirmation_required`, or once sign-in is
  // refused because the address has not been confirmed yet. Both need the same
  // "go read your email, then come back and sign in" screen.
  const [awaitingConfirmation, setAwaitingConfirmation] = useState("");
  const reducedMotion = useReducedMotion();

  function switchMode(next: "sign-in" | "sign-up") {
    setMode(next);
    setError("");
    setAwaitingConfirmation("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const result =
        mode === "sign-up"
          ? await catApi.signUp(email, password)
          : await catApi.signIn(email, password);
      if (result.status === "confirmation_required") {
        setAwaitingConfirmation(email);
        setPassword("");
        return;
      }
      await onAuthenticated(result);
    } catch (caught) {
      // Signing in before confirming is an expected outcome, not a failure the
      // user should read as "something broke".
      if (caught instanceof ApiError && caught.code === "EMAIL_NOT_CONFIRMED") {
        setAwaitingConfirmation(email);
        setPassword("");
        return;
      }
      if (
        caught instanceof ApiError &&
        caught.code === "EMAIL_ALREADY_REGISTERED"
      ) {
        setMode("sign-in");
        setError(caught.message);
        return;
      }
      setError(
        caught instanceof Error
          ? caught.message
          : "We could not open your account just now.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (awaitingConfirmation) {
    return (
      <main className="welcome-screen">
        <div className="welcome-brand">
          <span className="brand-mark" aria-hidden="true">
            <Heart size={17} fill="currentColor" />
          </span>
          <span>Whisker rooms</span>
        </div>
        <motion.section
          className="auth-card auth-card--confirm"
          initial={reducedMotion ? false : { opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          aria-labelledby="confirm-heading"
          role="status"
        >
          <span className="brand-mark" aria-hidden="true">
            <MailCheck size={18} />
          </span>
          <h2 id="confirm-heading">Check your email.</h2>
          <p>
            We sent a confirmation link to <strong>{awaitingConfirmation}</strong>.
            Open it to finish setting up your account, then come back and sign in.
          </p>
          <p className="welcome-footnote">
            Nothing arrived? Give it a minute, then look in spam. The link
            expires after 24 hours.
          </p>
          <button
            type="button"
            className="primary-button auth-submit"
            onClick={() => {
              setAwaitingConfirmation("");
              setMode("sign-in");
            }}
          >
            <span>I&rsquo;ve confirmed &mdash; sign me in</span>
            <ArrowRight size={18} aria-hidden="true" />
          </button>
        </motion.section>
      </main>
    );
  }

  return (
    <main className="welcome-screen">
      <div className="welcome-brand">
        <span className="brand-mark" aria-hidden="true">
          <Heart size={17} fill="currentColor" />
        </span>
        <span>Whisker rooms</span>
      </div>

      <div className="welcome-grid">
        <motion.section
          className="welcome-copy"
          initial={reducedMotion ? false : { opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        >
          <p className="eyebrow">A little more context. A little less guessing.</p>
          <h1>
            Meet your cat
            <br />
            <em>where they are.</em>
          </h1>
          <p className="welcome-lede">
            Four quiet corners for questions, careful health guidance, small
            discoveries, and the moments you never want to lose.
          </p>
          <div className="welcome-note">
            <ShieldCheck size={18} aria-hidden="true" />
            <p>
              This app offers information, not a diagnosis, and never replaces
              advice from a veterinarian.
            </p>
          </div>
        </motion.section>

        <motion.section
          className="auth-card"
          initial={reducedMotion ? false : { opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: reducedMotion ? 0 : 0.12, duration: 0.55 }}
          aria-labelledby="auth-heading"
        >
          <div className="auth-tabs" role="tablist" aria-label="Account access">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "sign-up"}
              className={mode === "sign-up" ? "active" : ""}
              onClick={() => switchMode("sign-up")}
            >
              Create account
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "sign-in"}
              className={mode === "sign-in" ? "active" : ""}
              onClick={() => switchMode("sign-in")}
            >
              Sign in
            </button>
          </div>
          <h2 id="auth-heading">
            {mode === "sign-up" ? "Make a room for them." : "Welcome back."}
          </h2>
          <p>
            {mode === "sign-up"
              ? "Start with one cat. You can add the rest of the household later."
              : "Your cats and their memories are waiting."}
          </p>
          <form onSubmit={submit}>
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                placeholder="you@example.com"
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete={
                  mode === "sign-up" ? "new-password" : "current-password"
                }
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                minLength={8}
                placeholder="At least 8 characters"
              />
            </label>
            {error ? (
              <p className="form-error" role="alert">
                {error}
              </p>
            ) : null}
            <button className="primary-button auth-submit" disabled={busy}>
              <span>
                {busy
                  ? "Opening your room…"
                  : mode === "sign-up"
                    ? "Create my account"
                    : "Sign in"}
              </span>
              <ArrowRight size={18} aria-hidden="true" />
            </button>
          </form>
        </motion.section>
      </div>

      <p className="welcome-footer">Made for the ordinary magic of living with cats.</p>
    </main>
  );
}
