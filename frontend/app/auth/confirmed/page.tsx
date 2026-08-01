import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Email confirmed — Whisker rooms",
  // A confirmation landing page carries auth fragments in the URL. Keep it out
  // of search results and out of referrer headers.
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

/**
 * Where Supabase returns the user after they follow the confirmation link;
 * this must match SUPABASE_EMAIL_REDIRECT_URL on the backend and be registered
 * under Supabase Auth -> URL Configuration -> Redirect URLs.
 *
 * Deliberately static and server-rendered. Supabase appends its token material
 * to the URL fragment, and this page never reads it: sign-in happens through
 * the backend `/auth/sign-in` wrapper so that account resolution and the
 * `AuthSessionResponse` contract stay on one path. The page's only job is to
 * tell the user the confirmation worked and send them back to sign in.
 */
export default function EmailConfirmedPage() {
  return (
    <main className="welcome-screen">
      <div className="welcome-brand">
        <span>Whisker rooms</span>
      </div>
      <section className="auth-card auth-card--confirm" role="status">
        <h2>Your email is confirmed.</h2>
        <p>
          That&rsquo;s the last setup step. Sign in and your cats&rsquo; rooms are
          ready.
        </p>
        <Link className="primary-button auth-submit" href="/">
          <span>Sign in</span>
        </Link>
      </section>
    </main>
  );
}
