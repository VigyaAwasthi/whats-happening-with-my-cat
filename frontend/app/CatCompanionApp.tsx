"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AnimatePresence,
  LayoutGroup,
  MotionConfig,
  motion,
} from "motion/react";
import { PawPrint } from "lucide-react";
import { ApiError, catApi, NO_ACTIVE_CAT_EVENT } from "@/lib/api";
import { attachSupabaseSession, getSupabase } from "@/lib/supabase";
import type {
  AppScreen,
  AuthSession,
  BehaviorMessage,
  CatProfile,
  HealthExchange,
} from "@/lib/types";
import { Welcome } from "@/components/cat-app/welcome";
import { ProfileManager } from "@/components/cat-app/profile";
import { Hub } from "@/components/cat-app/hub";
import { ChatCorner } from "@/components/cat-app/chat";
import { HealthCorner } from "@/components/cat-app/health";
import { FactsCorner } from "@/components/cat-app/facts";
import { MomentsCorner } from "@/components/cat-app/moments";

const AUTH_STORAGE_KEY = "whisker-rooms-session";

export function CatCompanionApp() {
  const [screen, setScreen] = useState<AppScreen>("welcome");
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [cats, setCats] = useState<CatProfile[]>([]);
  const [activeCatId, setActiveCatId] = useState("");
  const [loading, setLoading] = useState(true);
  const [behaviorSessionId, setBehaviorSessionId] = useState(() =>
    crypto.randomUUID(),
  );
  const [healthSessionId, setHealthSessionId] = useState(() =>
    crypto.randomUUID(),
  );
  const [behaviorMessages, setBehaviorMessages] = useState<BehaviorMessage[]>([]);
  const [healthExchanges, setHealthExchanges] = useState<HealthExchange[]>([]);

  const activeCat = useMemo(
    () => cats.find((cat) => cat.id === activeCatId) ?? cats[0] ?? null,
    [activeCatId, cats],
  );

  const loadCats = useCallback(async (token: string, preferredCatId?: string) => {
    const nextCats = await catApi.listCats(token);
    setCats(nextCats);
    setActiveCatId((current) =>
      (preferredCatId &&
        nextCats.find((cat) => cat.id === preferredCatId)?.id) ||
      nextCats.find((cat) => cat.id === current)?.id ||
      nextCats[0]?.id ||
      "",
    );
    if (!nextCats.length) setScreen("profile");
    return nextCats;
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function restore() {
      await Promise.resolve();
      if (cancelled) return;
      const stored = window.localStorage.getItem(AUTH_STORAGE_KEY);
      if (!stored) {
        setLoading(false);
        return;
      }
      try {
        const restored = JSON.parse(stored) as AuthSession;
        setAuth(restored);
        void attachSupabaseSession(restored).catch(() => undefined);
        const nextCats = await loadCats(restored.access_token);
        if (!cancelled) setScreen(nextCats.length ? "hub" : "profile");
      } catch {
        if (!cancelled) {
          window.localStorage.removeItem(AUTH_STORAGE_KEY);
          setAuth(null);
          setScreen("welcome");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void restore();
    return () => {
      cancelled = true;
    };
  }, [loadCats]);

  useEffect(() => {
    function routeToOnboarding() {
      setCats([]);
      setActiveCatId("");
      setBehaviorMessages([]);
      setHealthExchanges([]);
      setScreen("profile");
    }
    window.addEventListener(NO_ACTIVE_CAT_EVENT, routeToOnboarding);
    return () =>
      window.removeEventListener(NO_ACTIVE_CAT_EVENT, routeToOnboarding);
  }, []);

  async function completeAuthentication(session: AuthSession) {
    await attachSupabaseSession(session);
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
    setAuth(session);
    const nextCats = await loadCats(session.access_token);
    setScreen(nextCats.length ? "hub" : "profile");
  }

  function switchCat(catId: string) {
    if (catId === activeCatId) return;
    setActiveCatId(catId);
    setBehaviorSessionId(crypto.randomUUID());
    setHealthSessionId(crypto.randomUUID());
    setBehaviorMessages([]);
    setHealthExchanges([]);
  }

  function signOut() {
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
    void getSupabase()?.auth.signOut();
    setAuth(null);
    setCats([]);
    setActiveCatId("");
    setBehaviorMessages([]);
    setHealthExchanges([]);
    setScreen("welcome");
  }

  async function refreshCats(preferredCatId?: string) {
    if (!auth) return;
    try {
      await loadCats(auth.access_token, preferredCatId);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "NO_ACTIVE_CAT") {
        setScreen("profile");
        return;
      }
      throw caught;
    }
  }

  if (loading) {
    return (
      <main className="app-loading">
        <motion.div
          animate={{ rotate: [0, 10, -8, 0] }}
          transition={{ repeat: Infinity, duration: 1.7 }}
        >
          <PawPrint aria-hidden="true" />
        </motion.div>
        <p>Opening the rooms…</p>
      </main>
    );
  }

  return (
    <MotionConfig reducedMotion="user">
      <LayoutGroup id="whisker-rooms">
        <AnimatePresence initial={false} mode="sync">
          {!auth || screen === "welcome" ? (
            <Welcome key="welcome" onAuthenticated={completeAuthentication} />
          ) : screen === "profile" || !activeCat ? (
            <ProfileManager
              key="profile"
              token={auth.access_token}
              cats={cats}
              onCatsChanged={refreshCats}
              onEnterHub={() => setScreen("hub")}
              onSignOut={signOut}
            />
          ) : screen === "hub" ? (
            <Hub
              key="hub"
              cats={cats}
              activeCat={activeCat}
              onSwitchCat={switchCat}
              onManage={() => setScreen("profile")}
              onEnterCorner={(corner) => setScreen(corner)}
            />
          ) : screen === "behavior" ? (
            <ChatCorner
              key={`behavior-${activeCat.id}`}
              token={auth.access_token}
              cats={cats}
              activeCat={activeCat}
              sessionId={behaviorSessionId}
              messages={behaviorMessages}
              onSessionId={setBehaviorSessionId}
              onMessages={setBehaviorMessages}
              onSwitchCat={switchCat}
              onManage={() => setScreen("profile")}
              onBack={() => setScreen("hub")}
              onHealthNudge={() => setScreen("health")}
            />
          ) : screen === "health" ? (
            <HealthCorner
              key={`health-${activeCat.id}`}
              token={auth.access_token}
              cats={cats}
              activeCat={activeCat}
              sessionId={healthSessionId}
              exchanges={healthExchanges}
              onSessionId={setHealthSessionId}
              onExchanges={setHealthExchanges}
              onSwitchCat={switchCat}
              onManage={() => setScreen("profile")}
              onBack={() => setScreen("hub")}
            />
          ) : screen === "fun-facts" ? (
            <FactsCorner
              key={`facts-${activeCat.id}`}
              token={auth.access_token}
              cats={cats}
              activeCat={activeCat}
              onSwitchCat={switchCat}
              onManage={() => setScreen("profile")}
              onBack={() => setScreen("hub")}
            />
          ) : (
            <MomentsCorner
              key={`moments-${activeCat.id}`}
              token={auth.access_token}
              cats={cats}
              activeCat={activeCat}
              onSwitchCat={switchCat}
              onManage={() => setScreen("profile")}
              onBack={() => setScreen("hub")}
            />
          )}
        </AnimatePresence>
      </LayoutGroup>
    </MotionConfig>
  );
}
