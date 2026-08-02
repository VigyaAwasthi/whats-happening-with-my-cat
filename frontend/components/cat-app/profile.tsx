"use client";

import {
  ArrowLeft,
  ArrowRight,
  Camera,
  Download,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
import { catApi } from "@/lib/api";
import { ImageConversionError, prepareImageForUpload } from "@/lib/images";
import { uploadPreparedCatMedia } from "@/lib/supabase";
import type { CatProfile } from "@/lib/types";
import { EmptyPhoto, useSignedUrls } from "@/components/cat-app/shared";
import { CatEditor } from "@/components/cat-app/cat-editor";
import {
  CatFormFields,
  PhotoPicker,
  emptyCatForm,
  formToCreateInput,
  type CatFormValues,
} from "@/components/cat-app/cat-form";

type PreparedPhoto = {
  id: string;
  blob: Blob;
  extension: "webp" | "jpg";
  name: string;
  previewUrl: string;
};

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
  const [values, setValues] = useState<CatFormValues>(emptyCatForm);
  const [photos, setPhotos] = useState<PreparedPhoto[]>([]);
  const [intent, setIntent] = useState<"another" | "hub">("hub");
  const [busy, setBusy] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [editingCatId, setEditingCatId] = useState<string | null>(null);
  const [confirmAccountDelete, setConfirmAccountDelete] = useState(false);

  const editingCat = useMemo(
    () => cats.find((cat) => cat.id === editingCatId) ?? null,
    [cats, editingCatId],
  );

  useEffect(
    () => () => photos.forEach((photo) => URL.revokeObjectURL(photo.previewUrl)),
    [photos],
  );

  function update(patch: Partial<CatFormValues>) {
    setValues((current) => ({ ...current, ...patch }));
  }

  function resetForm() {
    setValues(emptyCatForm());
    setPhotos((current) => {
      current.forEach((photo) => URL.revokeObjectURL(photo.previewUrl));
      return [];
    });
  }

  /**
   * Convert on pick so an unreadable photo fails here, in front of the file
   * picker, instead of after the cat is saved. The preview shown below is the
   * converted blob, which makes the thumbnail itself the proof that the stored
   * image will render.
   */
  async function pickPhotos(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!selected.length) return;
    setPreparing(true);
    setError("");
    const room = Math.max(0, 6 - photos.length);
    const failures: string[] = [];
    const prepared: PreparedPhoto[] = [];
    for (const file of selected.slice(0, room)) {
      try {
        const result = await prepareImageForUpload(file);
        prepared.push({
          id: crypto.randomUUID(),
          blob: result.blob,
          extension: result.extension,
          name: file.name,
          previewUrl: URL.createObjectURL(result.blob),
        });
      } catch (caught) {
        failures.push(
          caught instanceof ImageConversionError
            ? `${file.name}: ${caught.message}`
            : `${file.name} could not be read.`,
        );
      }
    }
    setPhotos((current) => [...current, ...prepared]);
    if (failures.length) setError(failures.join(" "));
    setPreparing(false);
  }

  function removePhoto(id: string) {
    setPhotos((current) => {
      const target = current.find((photo) => photo.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return current.filter((photo) => photo.id !== id);
    });
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

    try {
      await catApi.createCat(token, formToCreateInput(values, catId, []));
      const keys: string[] = [];
      for (const photo of photos) {
        const { key } = await uploadPreparedCatMedia(
          catId,
          photo.blob,
          photo.name,
          photo.extension,
        );
        if (key) keys.push(key);
      }
      if (keys.length) {
        await catApi.patchCat(token, { cat_id: catId, photo_references: keys });
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
          <CatFormFields values={values} onChange={update} idPrefix="new" />

          <div className="form-section-heading">
            <span>03</span>
            <div>
              <h2>A face for the wall</h2>
              <p>Up to six photos. You can always add more later.</p>
            </div>
          </div>
          <PhotoPicker onPick={pickPhotos} busy={preparing} />
          {photos.length ? (
            <div className="photo-preview-row">
              {photos.map((photo, index) => (
                <div key={photo.id}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={photo.previewUrl} alt={`Selected cat photo ${index + 1}`} />
                  <button
                    type="button"
                    onClick={() => removePhoto(photo.id)}
                    aria-label={`Remove ${photo.name}`}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          ) : null}

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
              disabled={busy || preparing || cats.length >= 10}
              onClick={() => setIntent("another")}
            >
              <Plus size={17} aria-hidden="true" />
              Save &amp; add another
            </button>
            <button
              className="primary-button"
              type="submit"
              disabled={busy || preparing || cats.length >= 10}
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
                  onRequestDelete={() => setPendingDelete(cat.id)}
                  onCancelDelete={() => setPendingDelete(null)}
                  onDelete={() => removeCat(cat.id)}
                  onEdit={() => setEditingCatId(cat.id)}
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

      {editingCat ? (
        <CatEditor
          key={editingCat.id}
          token={token}
          cat={editingCat}
          onSaved={onCatsChanged}
          onClose={() => setEditingCatId(null)}
        />
      ) : null}
    </main>
  );
}

function HouseholdCat({
  cat,
  pending,
  onRequestDelete,
  onCancelDelete,
  onDelete,
  onEdit,
}: {
  cat: CatProfile;
  pending: boolean;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
  onEdit: () => void;
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
        <span className="household-meta">
          {cat.photo_references.length} photo
          {cat.photo_references.length === 1 ? "" : "s"}
        </span>
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
        <div className="household-cat-actions">
          <button type="button" className="icon-button" onClick={onEdit}>
            <Pencil size={16} aria-hidden="true" />
            <span className="sr-only">Edit {cat.name}</span>
          </button>
          <button type="button" className="icon-button" onClick={onRequestDelete}>
            <Trash2 size={16} aria-hidden="true" />
            <span className="sr-only">Remove {cat.name}</span>
          </button>
        </div>
      )}
    </article>
  );
}
