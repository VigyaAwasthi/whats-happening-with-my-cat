"use client";

import { Upload } from "lucide-react";
import type { ChangeEvent } from "react";
import {
  ACCEPTED_UPLOAD_LABEL,
  ACCEPTED_UPLOAD_TYPES,
} from "@/lib/images";
import type {
  BrandAccent,
  CatCreateInput,
  CatProfile,
  CatSex,
} from "@/lib/types";

/**
 * The single definition of a cat's editable fields, shared by the create form
 * and the edit form.
 *
 * The requirement is that the two "cannot drift apart". A shared *stylesheet*
 * would not achieve that — the way these forms drift is one of them gaining a
 * field the other never learns about. So the field list lives here once, as
 * both a type and a rendered component, and adding a field to `CatFormValues`
 * makes it appear in both forms with no further action.
 */
export type CatFormValues = {
  name: string;
  ageValue: string;
  ageUnit: "months" | "years";
  breed: string;
  sex: CatSex;
  weightValue: string;
  weightUnit: "kg" | "lb";
  energy: 1 | 2 | 3 | 4 | 5;
  patterns: string;
  conditions: string;
  accent: BrandAccent;
};

export const ACCENTS: { value: BrandAccent; name: string }[] = [
  { value: "#E43D12", name: "Vermillion" },
  { value: "#D6536D", name: "Raspberry" },
  { value: "#EFB11D", name: "Golden" },
  { value: "#FFA2B6", name: "Blush" },
];

export function emptyCatForm(): CatFormValues {
  return {
    name: "",
    ageValue: "3",
    ageUnit: "years",
    breed: "",
    sex: "unknown",
    weightValue: "9",
    weightUnit: "lb",
    energy: 3,
    patterns: "",
    conditions: "",
    accent: "#E43D12",
  };
}

/** Load an existing profile into the form. The inverse of `formToPatch`. */
export function catToForm(cat: CatProfile): CatFormValues {
  return {
    name: cat.name,
    ageValue: String(cat.age.value),
    ageUnit: cat.age.unit,
    breed: cat.breed ?? "",
    sex: cat.sex,
    weightValue: String(cat.weight.value),
    weightUnit: cat.weight.unit,
    energy: cat.energy_level,
    patterns: cat.common_patterns,
    conditions: cat.known_conditions.join(", "),
    accent: (ACCENTS.find((a) => a.value === cat.theme.primary_color)?.value ??
      "#E43D12") as BrandAccent,
  };
}

function conditionList(raw: string): string[] {
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

export function formToCreateInput(
  form: CatFormValues,
  catId: string,
  photoReferences: string[],
): CatCreateInput {
  return {
    cat_id: catId,
    name: form.name.trim(),
    age: { value: Number(form.ageValue), unit: form.ageUnit },
    breed: form.breed.trim() || null,
    sex: form.sex,
    weight: { value: Number(form.weightValue), unit: form.weightUnit },
    energy_level: form.energy,
    common_patterns: form.patterns.trim(),
    known_conditions: conditionList(form.conditions),
    photo_references: photoReferences,
    theme: { primary_color: form.accent, accent_color: form.accent },
  };
}

/**
 * Every field the backend `PATCH /cats` accepts, sent on every save.
 *
 * Sending the full set rather than a diff is deliberate: a diff would have to
 * decide what "unchanged" means for each field, and that decision is exactly
 * where a field silently stops being editable.
 */
export function formToPatch(
  form: CatFormValues,
  catId: string,
  photoReferences: string[],
) {
  return {
    cat_id: catId,
    name: form.name.trim(),
    age: { value: Number(form.ageValue), unit: form.ageUnit },
    breed: form.breed.trim() || null,
    sex: form.sex,
    weight: { value: Number(form.weightValue), unit: form.weightUnit },
    energy_level: form.energy,
    common_patterns: form.patterns.trim(),
    known_conditions: conditionList(form.conditions),
    photo_references: photoReferences,
    theme: { primary_color: form.accent, accent_color: form.accent },
  };
}

type CatFormFieldsProps = {
  values: CatFormValues;
  onChange: (patch: Partial<CatFormValues>) => void;
  /** Section numbers are shown on the create flow and hidden in the editor. */
  numbered?: boolean;
  idPrefix?: string;
};

export function CatFormFields({
  values,
  onChange,
  numbered = true,
  idPrefix = "cat",
}: CatFormFieldsProps) {
  return (
    <>
      {numbered ? (
        <div className="form-section-heading">
          <span>01</span>
          <div>
            <h2>The essentials</h2>
            <p>The things you know without thinking.</p>
          </div>
        </div>
      ) : null}
      <div className="form-grid">
        <label className="field-wide">
          Name
          <input
            id={`${idPrefix}-name`}
            value={values.name}
            onChange={(event) => onChange({ name: event.target.value })}
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
              value={values.ageValue}
              onChange={(event) => onChange({ ageValue: event.target.value })}
              required
              aria-label="Age value"
            />
            <select
              value={values.ageUnit}
              onChange={(event) =>
                onChange({ ageUnit: event.target.value as "months" | "years" })
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
              value={values.weightValue}
              onChange={(event) => onChange({ weightValue: event.target.value })}
              required
              aria-label="Weight value"
            />
            <select
              value={values.weightUnit}
              onChange={(event) =>
                onChange({ weightUnit: event.target.value as "kg" | "lb" })
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
            value={values.breed}
            onChange={(event) => onChange({ breed: event.target.value })}
            placeholder="Domestic shorthair, Bengal, a glorious mystery…"
          />
        </label>
        <label className="field-wide">
          Sex <span className="optional">optional</span>
          <select
            value={values.sex}
            onChange={(event) => onChange({ sex: event.target.value as CatSex })}
          >
            <option value="unknown">Not sure</option>
            <option value="female">Female</option>
            <option value="male">Male</option>
          </select>
        </label>
      </div>

      {numbered ? (
        <div className="form-section-heading">
          <span>02</span>
          <div>
            <h2>Their particular ways</h2>
            <p>There is no wrong kind of cat here.</p>
          </div>
        </div>
      ) : null}
      <fieldset className="energy-field">
        <legend>Energy level</legend>
        <div>
          {[1, 2, 3, 4, 5].map((level) => (
            <button
              type="button"
              key={level}
              className={values.energy === level ? "active" : ""}
              aria-pressed={values.energy === level}
              onClick={() => onChange({ energy: level as 1 | 2 | 3 | 4 | 5 })}
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
          value={values.patterns}
          onChange={(event) => onChange({ patterns: event.target.value })}
          rows={4}
          placeholder="Knocks pens off the desk. Sleeps under the radiator. Sprints after dinner."
        />
      </label>
      <label>
        Known conditions <span className="optional">comma-separated</span>
        <input
          value={values.conditions}
          onChange={(event) => onChange({ conditions: event.target.value })}
          placeholder="Asthma, food allergy"
        />
      </label>

      <fieldset className="accent-field">
        <legend>Room accent</legend>
        <p>The cream wall stays calm. This is their little signature.</p>
        <div>
          {ACCENTS.map((option) => (
            <button
              type="button"
              key={option.value}
              className={values.accent === option.value ? "active" : ""}
              aria-pressed={values.accent === option.value}
              onClick={() => onChange({ accent: option.value })}
            >
              <span style={{ backgroundColor: option.value }} />
              {option.name}
            </button>
          ))}
        </div>
      </fieldset>
    </>
  );
}

export function PhotoPicker({
  onPick,
  label = "Choose photos",
  busy = false,
}: {
  onPick: (event: ChangeEvent<HTMLInputElement>) => void;
  label?: string;
  busy?: boolean;
}) {
  return (
    <label className="photo-drop">
      <input
        type="file"
        // Matches what `prepareImageForUpload` can actually produce a
        // renderable image from. HEIC is offered because it is converted.
        accept={ACCEPTED_UPLOAD_TYPES}
        multiple
        onChange={onPick}
        className="sr-only"
        disabled={busy}
      />
      <Upload size={22} aria-hidden="true" />
      <span>{busy ? "Preparing photos…" : label}</span>
      <small>{ACCEPTED_UPLOAD_LABEL}</small>
    </label>
  );
}
