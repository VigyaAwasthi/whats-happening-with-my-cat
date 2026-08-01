"use client";

import {
  CalendarDays,
  Camera,
  Film,
  Plus,
  StickyNote,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
import { catApi } from "@/lib/api";
import {
  deleteCatMedia,
  uploadCatMedia,
} from "@/lib/supabase";
import type { CatProfile, Moment, MomentKind } from "@/lib/types";
import {
  CornerHeader,
  LoadingMark,
  useGentleScroll,
  useSignedUrls,
} from "@/components/cat-app/shared";

type MomentsCornerProps = {
  token: string;
  cats: CatProfile[];
  activeCat: CatProfile;
  onSwitchCat: (catId: string) => void;
  onManage: () => void;
  onBack: () => void;
};

const MOMENT_KINDS: {
  id: MomentKind;
  label: string;
  icon: typeof Camera;
}[] = [
  { id: "photo", label: "Photo", icon: Camera },
  { id: "video", label: "Video", icon: Film },
  { id: "note", label: "Note", icon: StickyNote },
  { id: "date", label: "Important date", icon: CalendarDays },
];

export function MomentsCorner({
  token,
  cats,
  activeCat,
  onSwitchCat,
  onManage,
  onBack,
}: MomentsCornerProps) {
  const [moments, setMoments] = useState<Moment[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [kind, setKind] = useState<MomentKind>("photo");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [eventDate, setEventDate] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [localMedia, setLocalMedia] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const reducedMotion = Boolean(useReducedMotion());
  useGentleScroll();

  const mediaKeys = useMemo(
    () =>
      moments
        .map((moment) => moment.media_key)
        .filter((key): key is string => Boolean(key)),
    [moments],
  );
  const mediaUrls = useSignedUrls(mediaKeys);

  useEffect(() => {
    let cancelled = false;
    catApi
      .moments(token, activeCat.id)
      .then((items) => {
        if (!cancelled) setMoments(items);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught.message
              : "The scrapbook could not be opened.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeCat.id, token]);

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
  }

  async function addMoment(event: FormEvent) {
    event.preventDefault();
    if ((kind === "photo" || kind === "video") && !file) {
      setError(`Choose a ${kind} to add.`);
      return;
    }
    setBusy(true);
    setError("");
    let uploaded: { key: string; previewUrl: string } | null = null;
    try {
      if (file && (kind === "photo" || kind === "video")) {
        uploaded = await uploadCatMedia(activeCat.id, file);
      }
      const created = await catApi.createMoment(token, {
        cat_id: activeCat.id,
        kind,
        title: title.trim(),
        body: body.trim() || null,
        media_key: uploaded?.key || null,
        event_date: eventDate || null,
      });
      if (uploaded?.previewUrl) {
        setLocalMedia((current) => ({
          ...current,
          [created.id]: uploaded!.previewUrl,
        }));
      }
      setMoments((current) => [created, ...current]);
      setShowForm(false);
      setTitle("");
      setBody("");
      setEventDate("");
      setFile(null);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "That moment could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function removeMoment(moment: Moment) {
    setError("");
    try {
      if (moment.media_key) await deleteCatMedia(moment.media_key);
      await catApi.deleteMoment(token, activeCat.id, moment.id);
      setMoments((current) => current.filter((item) => item.id !== moment.id));
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "That moment could not be removed.",
      );
    }
  }

  return (
    <motion.main
      layoutId={reducedMotion ? undefined : "corner-special-moments"}
      className="corner-room moments-room"
      transition={{ type: "spring", stiffness: 140, damping: 25, mass: 1 }}
    >
      <div className="dusk-lights" aria-hidden="true">
        {Array.from({ length: 15 }, (_, index) => (
          <span key={index} />
        ))}
      </div>
      <CornerHeader
        corner="special-moments"
        cats={cats}
        activeCat={activeCat}
        onChange={onSwitchCat}
        onManage={onManage}
        onBack={onBack}
        dark
      />

      <section className="moments-hero">
        <div>
          <p className="eyebrow">Only yours. Never read by AI.</p>
          <h1>
            The small archive
            <br />
            of <em>{activeCat.name}.</em>
          </h1>
        </div>
        <div>
          <p>
            Photos, notes, and dates kept exactly as you leave them. This corner
            is a scrapbook—nothing here enters chat or memory.
          </p>
          <button
            type="button"
            className="moment-add-button"
            onClick={() => setShowForm(true)}
          >
            <Plus size={17} aria-hidden="true" />
            Add a moment
          </button>
        </div>
      </section>

      {error ? (
        <p className="moments-error" role="alert">
          {error}
        </p>
      ) : null}
      {busy && !moments.length ? <LoadingMark label="Lighting the photo wall…" /> : null}

      {!busy && !moments.length ? (
        <section className="moments-empty">
          <Camera size={27} aria-hidden="true" />
          <h2>Leave the first light on.</h2>
          <p>Add a photo, a note, or a date you want to remember.</p>
          <button type="button" onClick={() => setShowForm(true)}>
            Add the first moment
          </button>
        </section>
      ) : (
        <section className="moments-wall" aria-label={`${activeCat.name}'s moments`}>
          {moments.map((moment, index) => (
            <MomentCard
              key={moment.id}
              moment={moment}
              mediaUrl={
                localMedia[moment.id] ||
                (moment.media_key ? mediaUrls[moment.media_key] : undefined)
              }
              index={index}
              onDelete={() => removeMoment(moment)}
            />
          ))}
        </section>
      )}

      <AnimatePresence>
        {showForm ? (
          <motion.div
            className="moment-form-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <motion.form
              className="moment-form"
              onSubmit={addMoment}
              initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 18 }}
            >
              <header>
                <div>
                  <p className="eyebrow">A new memory</p>
                  <h2>Add a moment</h2>
                </div>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => setShowForm(false)}
                >
                  <X size={18} aria-hidden="true" />
                  <span className="sr-only">Close</span>
                </button>
              </header>
              <fieldset className="moment-kind-picker">
                <legend>Kind</legend>
                <div>
                  {MOMENT_KINDS.map((item) => {
                    const Icon = item.icon;
                    return (
                      <button
                        type="button"
                        key={item.id}
                        className={kind === item.id ? "active" : ""}
                        aria-pressed={kind === item.id}
                        onClick={() => setKind(item.id)}
                      >
                        <Icon size={16} aria-hidden="true" />
                        {item.label}
                      </button>
                    );
                  })}
                </div>
              </fieldset>
              <label>
                Title
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  required
                  maxLength={200}
                  placeholder="Sunbeam nap"
                />
              </label>
              {(kind === "photo" || kind === "video") ? (
                <label className="moment-file">
                  <input
                    type="file"
                    accept={kind === "photo" ? "image/*" : "video/*"}
                    onChange={chooseFile}
                    className="sr-only"
                  />
                  <Upload size={18} aria-hidden="true" />
                  <span>{file ? file.name : `Choose a ${kind}`}</span>
                </label>
              ) : null}
              <label>
                Note <span className="optional">optional</span>
                <textarea
                  rows={4}
                  value={body}
                  onChange={(event) => setBody(event.target.value)}
                  placeholder="A few words for later…"
                />
              </label>
              {kind === "date" ? (
                <label>
                  Date
                  <input
                    type="date"
                    value={eventDate}
                    onChange={(event) => setEventDate(event.target.value)}
                    required
                  />
                </label>
              ) : null}
              <button className="moment-save" type="submit" disabled={busy}>
                {busy ? "Saving…" : "Keep this moment"}
              </button>
            </motion.form>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.main>
  );
}

function MomentCard({
  moment,
  mediaUrl,
  index,
  onDelete,
}: {
  moment: Moment;
  mediaUrl?: string;
  index: number;
  onDelete: () => void;
}) {
  const date = moment.event_date
    ? new Date(`${moment.event_date}T12:00:00`).toLocaleDateString(undefined, {
        month: "long",
        day: "numeric",
        year: "numeric",
      })
    : new Date(moment.created_at).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      });
  return (
    <motion.article
      className={`moment-card moment-${moment.kind} moment-tilt-${(index % 5) + 1}`}
      whileHover={{ y: -8, rotate: 0 }}
      whileFocus={{ y: -8, rotate: 0 }}
      tabIndex={0}
    >
      {moment.kind === "photo" && mediaUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={mediaUrl} alt={moment.title} />
      ) : null}
      {moment.kind === "video" && mediaUrl ? (
        <video src={mediaUrl} controls preload="metadata">
          <track kind="captions" />
        </video>
      ) : null}
      {moment.kind === "note" ? (
        <StickyNote size={21} aria-hidden="true" />
      ) : null}
      {moment.kind === "date" ? (
        <CalendarDays size={24} aria-hidden="true" />
      ) : null}
      <div className="moment-card-copy">
        <span>{date}</span>
        <h2>{moment.title}</h2>
        {moment.body ? <p>{moment.body}</p> : null}
      </div>
      <button type="button" className="moment-delete" onClick={onDelete}>
        <Trash2 size={15} aria-hidden="true" />
        <span className="sr-only">Delete {moment.title}</span>
      </button>
    </motion.article>
  );
}
