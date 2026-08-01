"use client";

import {
  ArrowLeft,
  ArrowRight,
  Camera,
  Download,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
import { catApi } from "@/lib/api";
import { uploadCatMedia } from "@/lib/supabase";
import type {
  BrandAccent,
  CatCreateInput,
  CatProfile,
  CatSex,
} from "@/lib/types";
import { EmptyPhoto, useSignedUrls } from "@/components/cat-app/shared";

const ACCENTS: { value: BrandAccent; name: string }[] = [
  { value: "#E43D12", name: "Vermillion" },
  { value: "#D6536D", name: "Raspberry" },
  { value: "#EFB11D", name: "Golden" },
  { value: "#FFA2B6", name: "Blush" },
];

type ProfileManagerProps = {
  token: string;
  cats: CatProfile[];
  onCatsChanged: (preferredCatId?: string) => Promise<void>;
  onEnterHub: () => void;
  onSignOut: () => void;
};

export function ProfileManager({
  token,
  cats,
  onCatsChanged,
  onEnterHub,
  onSignOut,
}: ProfileManagerProps) {
  const [name, setName] = useState("");
  const [ageValue, setAgeValue] = useState("3");
  const [ageUnit, setAgeUnit] = useState<"months" | "years">("years");
  const [breed, setBreed] = useState("");
  const [sex, setSex] = useState<CatSex>("unknown");
  const [weightValue, setWeightValue] = useState("9");
  const [weightUnit, setWeightUnit] = useState<"kg" | "lb">("lb");
  const [energy, setEnergy] = useState<1 | 2 | 3 | 4 | 5>(3);
  const [patterns, setPatterns] = useState("");
  const [conditions, setConditions] = useState("");
  const [accent, setAccent] = useState<BrandAccent>("#E43D12");
  const [photos, setPhotos] = useState<File[]>([]);
  const [intent, setIntent] = useState<"another" | "hub">("hub");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [updatingSex, setUpdatingSex] = useState<string | null>(null);
  const [confirmAccountDelete, setConfirmAccountDelete] = useState(false);
  const photoPreviews = useMemo(
    () => photos.map((photo) => URL.createObjectURL(photo)),
    [photos],
  );

  useEffect(
    () => () => photoPreviews.forEach((url) => URL.revokeObjectURL(url)),
    [photoPreviews],
  );

  function resetForm() {
    setName("");
    setAgeValue("3");
    setAgeUnit("years");
    setBreed("");
    setSex("unknown");
    setWeightValue("9");
    setWeightUnit("lb");
    setEnergy(3);
    setPatterns("");
    setConditions("");
    setAccent("#E43D12");
    setPhotos([]);
  }

  function pickPhotos(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    setPhotos((current) => [...current, ...selected].slice(0, 6));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (cats.length >= 10) {
      setError("Ten cats is the household limit. Remove one before adding another.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    const catId = crypto.randomUUID();
    const input: CatCreateInput = {
      cat_id: catId,
      name: name.trim(),
      age: { value: Number(ageValue), unit: ageUnit },
      breed: breed.trim() || null,
      sex,
      weight: { value: Number(weightValue), unit: weightUnit },
      energy_level: energy,
      common_patterns: patterns.trim(),
      known_conditions: conditions
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
      photo_references: [],
      theme: { primary_color: accent, accent_color: accent },
    };

    try {
      await catApi.createCat(token, input);
      const uploaded = await Promise.all(
        photos.map((photo) => uploadCatMedia(catId, photo)),
      );
      const keys = uploaded.map((item) => item.key).filter(Boolean);
      if (keys.length) {
        await catApi.patchCat(token, {
          cat_id: catId,
          photo_references: keys,
        });
      } else if (photos.length) {
        setNotice(
          "Your cat is saved. Photo previews stay on this device until real Supabase auth is enabled.",
        );
      }
      await onCatsChanged(catId);
      if (intent === "another" && cats.length < 9) {
        resetForm();
        window.scrollTo({ top: 0, behavior: "smooth" });
      } else {
        onEnterHub();
      }
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "We could not save this cat.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function removeCat(catId: string) {
    setError("");
    try {
      await catApi.deleteCat(token, catId);
      setPendingDelete(null);
      await onCatsChanged();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "We could not remove this cat.",
      );
    }
  }

  async function updateSex(cat: CatProfile, nextSex: CatSex) {
    setUpdatingSex(cat.id);
    setError("");
    setNotice("");
    try {
      await catApi.patchCat(token, { cat_id: cat.id, sex: nextSex });
      await onCatsChanged(cat.id);
      setNotice(`${cat.name}'s profile is updated.`);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : `We could not update ${cat.name}'s profile.`,
      );
    } finally {
      setUpdatingSex(null);
    }
  }

  async function exportAccount() {
    try {
      const data = await catApi.exportAccount(token);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], {
          type: "application/json",
        }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "whisker-rooms-export.json";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Export was not available.",
      );
    }
  }

  async function deleteAccount() {
    setBusy(true);
    try {
      await catApi.deleteAccount(token);
      onSignOut();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Your account could not be deleted safely.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="profile-screen">
      <header className="profile-header">
        {cats.length ? (
          <button type="button" className="back-button" onClick={onEnterHub}>
            <ArrowLeft size={18} aria-hidden="true" />
            Back to the wall
          </button>
        ) : (
          <div className="brand-wordmark">Whisker rooms</div>
        )}
        <span>{cats.length}/10 cats</span>
      </header>

      <section className="profile-intro">
        <p className="eyebrow">
          {cats.length ? "Make another room" : "First, tell us who lives here"}
        </p>
        <h1>
          A room should feel
          <br />
          <em>like their room.</em>
        </h1>
        <p>
          A few details help keep every answer grounded in the right cat. Their
          conversations and memories always stay separate.
        </p>
      </section>

      <div className="profile-layout">
        <form className="cat-form" onSubmit={submit}>
          <div className="form-section-heading">
            <span>01</span>
            <div>
              <h2>The essentials</h2>
              <p>The things you know without thinking.</p>
            </div>
          </div>
          <div className="form-grid">
            <label className="field-wide">
              Name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
                maxLength={100}
                placeholder="Mochi"
              />
            </label>
            <label>
              Age
              <span className="joined-fields">
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  value={ageValue}
                  onChange={(event) => setAgeValue(event.target.value)}
                  required
                  aria-label="Age value"
                />
                <select
                  value={ageUnit}
                  onChange={(event) =>
                    setAgeUnit(event.target.value as "months" | "years")
                  }
                  aria-label="Age unit"
                >
                  <option value="months">months</option>
                  <option value="years">years</option>
                </select>
              </span>
            </label>
            <label>
              Weight
              <span className="joined-fields">
                <input
                  type="number"
                  min="0.1"
                  step="0.1"
                  value={weightValue}
                  onChange={(event) => setWeightValue(event.target.value)}
                  required
                  aria-label="Weight value"
                />
                <select
                  value={weightUnit}
                  onChange={(event) =>
                    setWeightUnit(event.target.value as "kg" | "lb")
                  }
                  aria-label="Weight unit"
                >
                  <option value="lb">lb</option>
                  <option value="kg">kg</option>
                </select>
              </span>
            </label>
            <label className="field-wide">
              Breed <span className="optional">optional</span>
              <input
                value={breed}
                onChange={(event) => setBreed(event.target.value)}
                placeholder="Domestic shorthair, Bengal, a glorious mystery…"
              />
            </label>
            <label className="field-wide">
              Sex <span className="optional">optional</span>
              <select
                value={sex}
                onChange={(event) => setSex(event.target.value as CatSex)}
              >
                <option value="unknown">Not sure</option>
                <option value="female">Female</option>
                <option value="male">Male</option>
              </select>
            </label>
          </div>

          <div className="form-section-heading">
            <span>02</span>
            <div>
              <h2>Their particular ways</h2>
              <p>There is no wrong kind of cat here.</p>
            </div>
          </div>
          <fieldset className="energy-field">
            <legend>Energy level</legend>
            <div>
              {[1, 2, 3, 4, 5].map((level) => (
                <button
                  type="button"
                  key={level}
                  className={energy === level ? "active" : ""}
                  aria-pressed={energy === level}
                  onClick={() => setEnergy(level as 1 | 2 | 3 | 4 | 5)}
                >
                  {level}
                </button>
              ))}
            </div>
            <span>
              <small>quiet observer</small>
              <small>tiny weather system</small>
            </span>
          </fieldset>
          <label>
            Common patterns
            <textarea
              value={patterns}
              onChange={(event) => setPatterns(event.target.value)}
              rows={4}
              placeholder="Knocks pens off the desk. Sleeps under the radiator. Sprints after dinner."
            />
          </label>
          <label>
            Known conditions <span className="optional">comma-separated</span>
            <input
              value={conditions}
              onChange={(event) => setConditions(event.target.value)}
              placeholder="Asthma, food allergy"
            />
          </label>

          <div className="form-section-heading">
            <span>03</span>
            <div>
              <h2>A face for the wall</h2>
              <p>Up to six photos. You can always add more later.</p>
            </div>
          </div>
          <label className="photo-drop">
            <input
              type="file"
              accept="image/*"
              multiple
              onChange={pickPhotos}
              className="sr-only"
            />
            <Upload size={22} aria-hidden="true" />
            <span>Choose photos</span>
            <small>JPG, PNG, HEIC or WebP</small>
          </label>
          {photoPreviews.length ? (
            <div className="photo-preview-row">
              {photoPreviews.map((url, index) => (
                <div key={url}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={url} alt={`Selected cat photo ${index + 1}`} />
                </div>
              ))}
            </div>
          ) : null}

          <fieldset className="accent-field">
            <legend>Room accent</legend>
            <p>The cream wall stays calm. This is their little signature.</p>
            <div>
              {ACCENTS.map((option) => (
                <button
                  type="button"
                  key={option.value}
                  className={accent === option.value ? "active" : ""}
                  aria-pressed={accent === option.value}
                  onClick={() => setAccent(option.value)}
                >
                  <span style={{ backgroundColor: option.value }} />
                  {option.name}
                </button>
              ))}
            </div>
          </fieldset>

          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          {notice ? <p className="form-notice">{notice}</p> : null}
          <div className="profile-actions">
            <button
              className="secondary-button"
              type="submit"
              disabled={busy || cats.length >= 10}
              onClick={() => setIntent("another")}
            >
              <Plus size={17} aria-hidden="true" />
              Save & add another
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={busy || cats.length >= 10}
              onClick={() => setIntent("hub")}
            >
              {busy ? "Making their room…" : "Save & enter the wall"}
              <ArrowRight size={17} aria-hidden="true" />
            </button>
          </div>
        </form>

        <aside className="household-panel">
          <div>
            <p className="eyebrow">Your household</p>
            <h2>{cats.length ? "Rooms already made" : "The wall is waiting"}</h2>
          </div>
          {cats.length ? (
            <div className="household-list">
              {cats.map((cat) => (
                <HouseholdCat
                  key={cat.id}
                  cat={cat}
                  pending={pendingDelete === cat.id}
                  updatingSex={updatingSex === cat.id}
                  onRequestDelete={() => setPendingDelete(cat.id)}
                  onCancelDelete={() => setPendingDelete(null)}
                  onDelete={() => removeCat(cat.id)}
                  onSexChange={(nextSex) => updateSex(cat, nextSex)}
                />
              ))}
            </div>
          ) : (
            <div className="empty-household">
              <Camera size={28} aria-hidden="true" />
              <p>
                Once you save a cat, their photos become the heart of the hub.
              </p>
            </div>
          )}

          {cats.length >= 10 ? (
            <p className="cap-note">
              All ten rooms are full. Remove a cat before adding another.
            </p>
          ) : null}

          <div className="account-tools">
            <h3>Your data</h3>
            <button type="button" onClick={exportAccount}>
              <Download size={17} aria-hidden="true" />
              Download account data
            </button>
            {!confirmAccountDelete ? (
              <button
                type="button"
                className="danger-link"
                onClick={() => setConfirmAccountDelete(true)}
              >
                <Trash2 size={17} aria-hidden="true" />
                Delete account
              </button>
            ) : (
              <div className="delete-confirm" role="alert">
                <p>
                  This permanently deletes every cat, conversation, memory, and
                  moment.
                </p>
                <div>
                  <button
                    type="button"
                    onClick={() => setConfirmAccountDelete(false)}
                  >
                    Keep my account
                  </button>
                  <button type="button" onClick={deleteAccount} disabled={busy}>
                    Yes, delete everything
                  </button>
                </div>
              </div>
            )}
            <button type="button" className="quiet-link" onClick={onSignOut}>
              Sign out on this device
            </button>
          </div>
        </aside>
      </div>
    </main>
  );
}

function HouseholdCat({
  cat,
  pending,
  updatingSex,
  onRequestDelete,
  onCancelDelete,
  onDelete,
  onSexChange,
}: {
  cat: CatProfile;
  pending: boolean;
  updatingSex: boolean;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
  onSexChange: (sex: CatSex) => void;
}) {
  const urls = useSignedUrls(cat.photo_references.slice(0, 1));
  const photo = cat.photo_references[0]
    ? urls[cat.photo_references[0]]
    : undefined;
  return (
    <article className="household-cat">
      {photo ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={photo} alt={cat.name} />
      ) : (
        <EmptyPhoto cat={cat} />
      )}
      <div>
        <strong>{cat.name}</strong>
        <span>
          {cat.age.value} {cat.age.unit}
          {cat.breed ? ` · ${cat.breed}` : ""}
        </span>
        <label className="household-sex">
          <span>Sex</span>
          <select
            value={cat.sex ?? "unknown"}
            disabled={updatingSex}
            onChange={(event) => onSexChange(event.target.value as CatSex)}
            aria-label={`Sex for ${cat.name}`}
          >
            <option value="unknown">Not sure</option>
            <option value="female">Female</option>
            <option value="male">Male</option>
          </select>
        </label>
      </div>
      {pending ? (
        <div className="cat-delete-actions">
          <button type="button" onClick={onCancelDelete}>
            Keep
          </button>
          <button type="button" onClick={onDelete}>
            Remove
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="icon-button"
          onClick={onRequestDelete}
        >
          <Trash2 size={16} aria-hidden="true" />
          <span className="sr-only">Remove {cat.name}</span>
        </button>
      )}
    </article>
  );
}
