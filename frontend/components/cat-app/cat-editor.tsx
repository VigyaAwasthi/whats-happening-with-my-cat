"use client";

import { ChevronLeft, ChevronRight, Star, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";
import { catApi } from "@/lib/api";
import { ImageConversionError, prepareImageForUpload } from "@/lib/images";
import { deleteCatMedia, uploadPreparedCatMedia } from "@/lib/supabase";
import type { CatProfile } from "@/lib/types";
import { useSignedUrls } from "@/components/cat-app/shared";
import {
  CatFormFields,
  PhotoPicker,
  catToForm,
  formToPatch,
  type CatFormValues,
} from "@/components/cat-app/cat-form";

type PreparedPhoto = {
  id: string;
  blob: Blob;
  extension: "webp" | "jpg";
  name: string;
  previewUrl: string;
};

type CatEditorProps = {
  token: string;
  cat: CatProfile;
  onSaved: (catId: string) => Promise<void>;
  onClose: () => void;
};

/**
 * Edit every field of an existing cat, plus full photo management.
 *
 * The form body is `CatFormFields`, the same component the create flow renders,
 * so a field cannot exist in one and not the other.
 *
 * Note on memory: saving here calls `PATCH /cats` only. It does not touch
 * session memory, long-term memory, or stored conversations. Correcting a cat's
 * age changes what future answers are grounded in; it does not rewrite what was
 * already said. That is intentional — past conversations are a record, not a
 * projection of current state.
 */
export function CatEditor({ token, cat, onSaved, onClose }: CatEditorProps) {
  const [values, setValues] = useState<CatFormValues>(() => catToForm(cat));
  // Working copy of the storage keys. Nothing is written until save.
  const [keptKeys, setKeptKeys] = useState<string[]>(cat.photo_references);
  const [removedKeys, setRemovedKeys] = useState<string[]>([]);
  const [added, setAdded] = useState<PreparedPhoto[]>([]);
  const [busy, setBusy] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");

  const signedUrls = useSignedUrls(keptKeys);

  // No effect resets this state when a different cat is chosen: the caller
  // mounts this component with `key={cat.id}`, so switching cats remounts it
  // and the initializers above run fresh. That also guarantees a half-finished
  // edit can never leak onto another cat.

  useEffect(
    () => () => added.forEach((photo) => URL.revokeObjectURL(photo.previewUrl)),
    [added],
  );

  const totalPhotos = keptKeys.length + added.length;

  function update(patch: Partial<CatFormValues>) {
    setValues((current) => ({ ...current, ...patch }));
  }

  /**
   * Convert at pick time, not at save time. The converted blob is what becomes
   * the preview, so the thumbnail on screen is proof the stored image renders —
   * and a HEIC we cannot read fails here, while the user is still looking at the
   * file picker, rather than after a "saved" confirmation.
   */
  async function pickPhotos(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!selected.length) return;
    setPreparing(true);
    setError("");
    const room = Math.max(0, 6 - totalPhotos);
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
    setAdded((current) => [...current, ...prepared]);
    if (failures.length) setError(failures.join(" "));
    if (selected.length > room) {
      setError((current) =>
        `${current} Six photos is the maximum for one cat.`.trim(),
      );
    }
    setPreparing(false);
  }

  function moveKey(index: number, direction: -1 | 1) {
    setKeptKeys((current) => {
      const next = [...current];
      const target = index + direction;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function makePrimary(index: number) {
    setKeptKeys((current) => {
      if (index === 0) return current;
      const next = [...current];
      const [chosen] = next.splice(index, 1);
      return [chosen, ...next];
    });
  }

  function removeExisting(key: string) {
    setKeptKeys((current) => current.filter((item) => item !== key));
    // Queued, not deleted. Storage is only touched after the patch succeeds, so
    // a failed save never destroys a photo the user still has.
    setRemovedKeys((current) => [...current, key]);
  }

  function removeAdded(id: string) {
    setAdded((current) => {
      const target = current.find((photo) => photo.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return current.filter((photo) => photo.id !== id);
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const uploaded: string[] = [];
      for (const photo of added) {
        const { key } = await uploadPreparedCatMedia(
          cat.id,
          photo.blob,
          photo.name,
          photo.extension,
        );
        if (key) uploaded.push(key);
      }
      await catApi.patchCat(token, formToPatch(values, cat.id, [...keptKeys, ...uploaded]));

      // Only now is the storage object safe to destroy.
      for (const key of removedKeys) {
        try {
          await deleteCatMedia(key);
        } catch {
          // The row no longer references it, so the user sees the right thing.
          // An orphaned object is a cleanup concern, not a user-facing failure.
        }
      }
      await onSaved(cat.id);
      onClose();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : `We could not update ${cat.name}'s profile.`,
      );
    } finally {
      setBusy(false);
    }
  }

  const photoTiles = useMemo(
    () =>
      keptKeys.map((key, index) => ({
        key,
        url: signedUrls[key],
        index,
        primary: index === 0,
      })),
    [keptKeys, signedUrls],
  );

  return (
    <div className="cat-editor-backdrop" role="dialog" aria-modal="true" aria-label={`Edit ${cat.name}`}>
      <form className="cat-editor cat-form" onSubmit={save}>
        <header className="cat-editor-header">
          <div>
            <p className="eyebrow">Editing</p>
            <h2>{cat.name}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose}>
            <X size={20} aria-hidden="true" />
            <span className="sr-only">Close editor</span>
          </button>
        </header>

        <CatFormFields
          values={values}
          onChange={update}
          numbered={false}
          idPrefix={`edit-${cat.id}`}
        />

        <fieldset className="photo-manager">
          <legend>Photos</legend>
          <p>
            The first photo leads the wall. Reorder them, or choose a different
            one to lead.
          </p>

          {photoTiles.length || added.length ? (
            <ul className="photo-manager-grid">
              {photoTiles.map((tile) => (
                <li key={tile.key} className={tile.primary ? "is-primary" : ""}>
                  {tile.url ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img src={tile.url} alt={`${cat.name} photo ${tile.index + 1}`} />
                  ) : (
                    <div className="photo-placeholder" aria-hidden="true" />
                  )}
                  {tile.primary ? <span className="primary-badge">Leads the wall</span> : null}
                  <div className="photo-actions">
                    <button
                      type="button"
                      onClick={() => moveKey(tile.index, -1)}
                      disabled={tile.index === 0}
                      aria-label={`Move photo ${tile.index + 1} earlier`}
                    >
                      <ChevronLeft size={15} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => makePrimary(tile.index)}
                      disabled={tile.primary}
                      aria-label={`Make photo ${tile.index + 1} lead the wall`}
                    >
                      <Star size={15} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => moveKey(tile.index, 1)}
                      disabled={tile.index === keptKeys.length - 1}
                      aria-label={`Move photo ${tile.index + 1} later`}
                    >
                      <ChevronRight size={15} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => removeExisting(tile.key)}
                      aria-label={`Remove photo ${tile.index + 1}`}
                    >
                      <Trash2 size={15} aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
              {added.map((photo) => (
                <li key={photo.id} className="is-new">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={photo.previewUrl} alt={`New photo ${photo.name}`} />
                  <span className="primary-badge">New</span>
                  <div className="photo-actions">
                    <button
                      type="button"
                      className="danger"
                      onClick={() => removeAdded(photo.id)}
                      aria-label={`Remove new photo ${photo.name}`}
                    >
                      <Trash2 size={15} aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="photo-manager-empty">No photos yet.</p>
          )}

          {totalPhotos < 6 ? (
            <PhotoPicker onPick={pickPhotos} label="Add photos" busy={preparing} />
          ) : null}
        </fieldset>

        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="profile-actions">
          <button type="button" className="secondary-button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary-button" type="submit" disabled={busy || preparing}>
            {busy ? "Saving…" : "Save changes"}
          </button>
        </div>
      </form>
    </div>
  );
}
