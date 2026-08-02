"use client";

/**
 * Turn whatever a phone hands us into something a browser will actually render.
 *
 * The bug this fixes: iPhones shoot HEIC by default. HEIC uploaded fine and
 * then failed to render everywhere except Safari, so the user saw a successful
 * upload followed by a broken image. That is worse than rejecting the file,
 * because the user believes their photo is safe.
 *
 * The rule here is: **nothing is uploaded until it has been decoded and
 * re-encoded in this browser.** Decoding is the proof that it can be
 * displayed. A file that cannot be decoded is rejected at upload time with a
 * message, never stored.
 */

/** Formats we store. Both render in every browser we support. */
const OUTPUT_MIME = "image/webp";
const OUTPUT_FALLBACK_MIME = "image/jpeg";
const OUTPUT_QUALITY = 0.86;

/** Longest edge of a stored photo. The photo wall never needs more. */
const MAX_EDGE = 2048;

/**
 * What the file picker offers. HEIC is included because we convert it, and
 * `image/*` alone is not enough: iOS reports HEIC inconsistently, and some
 * Android pickers filter `image/*` down to formats they can preview.
 */
export const ACCEPTED_UPLOAD_TYPES =
  "image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif,.heic,.heif";

export const ACCEPTED_UPLOAD_LABEL = "JPG, PNG, WebP, GIF, or HEIC";

export class ImageConversionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ImageConversionError";
  }
}

/**
 * HEIC detection cannot rely on the MIME type. iOS sometimes reports HEIC as
 * `image/heic`, sometimes `image/heif`, and sometimes as an empty string when
 * the file arrives via a share sheet or a Files-app pick. The extension is the
 * only consistently present signal, so both are checked.
 */
export function isHeic(file: File): boolean {
  const type = file.type.toLowerCase();
  if (type === "image/heic" || type === "image/heif") return true;
  if (type.startsWith("image/") && type !== "image/heic") return false;
  return /\.(heic|heif)$/i.test(file.name);
}

/**
 * EXIF orientation, or 1 when absent/unreadable.
 *
 * This matters specifically because of the conversion path. A browser applies
 * EXIF orientation automatically when it renders a JPEG into an `<img>`, but
 * `canvas.drawImage` does not — it draws raw pixels. So the moment we route an
 * image through a canvas to re-encode it, orientation is silently dropped and
 * the photo lands sideways. This is the single most common HEIC-conversion
 * complaint, and it is why the transform below is applied explicitly.
 */
export async function readExifOrientation(file: Blob): Promise<number> {
  // 64 KB is comfortably past the EXIF block in any camera image.
  const header = await file.slice(0, 65536).arrayBuffer();
  const view = new DataView(header);
  if (view.byteLength < 4) return 1;
  if (view.getUint16(0, false) !== 0xffd8) return 1; // not a JPEG

  let offset = 2;
  while (offset + 4 <= view.byteLength) {
    const marker = view.getUint16(offset, false);
    offset += 2;
    if (marker === 0xffe1) {
      // APP1
      if (offset + 8 > view.byteLength) return 1;
      if (view.getUint32(offset + 2, false) !== 0x45786966) return 1; // "Exif"
      const tiff = offset + 8;
      if (tiff + 8 > view.byteLength) return 1;
      const little = view.getUint16(tiff, false) === 0x4949;
      const dirStart = tiff + view.getUint32(tiff + 4, little);
      if (dirStart + 2 > view.byteLength) return 1;
      const entries = view.getUint16(dirStart, little);
      for (let i = 0; i < entries; i += 1) {
        const entry = dirStart + 2 + i * 12;
        if (entry + 12 > view.byteLength) return 1;
        if (view.getUint16(entry, little) === 0x0112) {
          const value = view.getUint16(entry + 8, little);
          return value >= 1 && value <= 8 ? value : 1;
        }
      }
      return 1;
    }
    if ((marker & 0xff00) !== 0xff00) return 1;
    if (offset + 2 > view.byteLength) return 1;
    offset += view.getUint16(offset, false);
  }
  return 1;
}

/** Whether an EXIF orientation swaps the image's width and height. */
export function orientationSwapsAxes(orientation: number): boolean {
  return orientation >= 5 && orientation <= 8;
}

/**
 * The canvas transform that undoes an EXIF orientation.
 *
 * `sourceWidth`/`sourceHeight` are the dimensions of the image being drawn —
 * the *un-rotated* source, not the oriented canvas. Passing the swapped canvas
 * dimensions instead puts the translation on the wrong axis and pushes the
 * image off-canvas for orientations 5-8.
 *
 * Exported separately from the canvas work so the maths can be unit tested in
 * Node, where there is no canvas. Returns the arguments for `setTransform`.
 */
export function orientationTransform(
  orientation: number,
  sourceWidth: number,
  sourceHeight: number,
): [number, number, number, number, number, number] {
  const width = sourceWidth;
  const height = sourceHeight;
  switch (orientation) {
    case 2:
      return [-1, 0, 0, 1, width, 0]; // mirror horizontal
    case 3:
      return [-1, 0, 0, -1, width, height]; // rotate 180
    case 4:
      return [1, 0, 0, -1, 0, height]; // mirror vertical
    case 5:
      return [0, 1, 1, 0, 0, 0]; // transpose
    case 6:
      return [0, 1, -1, 0, height, 0]; // rotate 90 CW
    case 7:
      return [0, -1, -1, 0, height, width]; // transverse
    case 8:
      return [0, -1, 1, 0, 0, width]; // rotate 270 CW
    default:
      return [1, 0, 0, 1, 0, 0];
  }
}

/** Scale a source down so its longest edge is at most `maxEdge`. */
export function fitWithin(
  width: number,
  height: number,
  maxEdge: number = MAX_EDGE,
): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= maxEdge) return { width, height };
  const scale = maxEdge / longest;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

export type ConvertedImage = {
  blob: Blob;
  extension: "webp" | "jpg";
  width: number;
  height: number;
  converted: boolean;
};

/**
 * Does this browser's decoder already apply EXIF orientation for us?
 *
 * This has to be measured, not assumed, and it is the crux of getting rotation
 * right. Applying our transform to pixels the decoder already rotated turns a
 * correct photo into a wrong one — a double rotation that lands 90° back where
 * it started. Guessing wrong in either direction produces a sideways photo.
 *
 * Chrome honours `imageOrientation: "none"` and hands back raw pixels. WebKit
 * ignores the hint entirely and always auto-orients — verified directly in
 * Safari, where `"none"`, `"from-image"`, and the default all returned
 * identical, already-rotated dimensions. So the only reliable answer comes from
 * decoding a probe image whose orientation we control and seeing what happens.
 *
 * Probed once per session and cached.
 */
let orientationProbe: Promise<boolean> | null = null;

function buildOrientationProbe(): Promise<Blob | null> {
  // A 2x1 JPEG tagged orientation=6. If the decoder applies it, the result
  // decodes as 1x2.
  const canvas = document.createElement("canvas");
  canvas.width = 2;
  canvas.height = 1;
  const context = canvas.getContext("2d");
  if (!context) return Promise.resolve(null);
  context.fillStyle = "#c33";
  context.fillRect(0, 0, 2, 1);
  return new Promise((resolve) => {
    canvas.toBlob(async (blob) => {
      if (!blob) return resolve(null);
      const bytes = new Uint8Array(await blob.arrayBuffer());
      // APP1/Exif, big-endian TIFF, one IFD0 entry: 0x0112 Orientation = 6.
      const exif = Uint8Array.from([
        0xff, 0xe1, 0x00, 0x22, 0x45, 0x78, 0x69, 0x66, 0x00, 0x00,
        0x4d, 0x4d, 0x00, 0x2a, 0x00, 0x00, 0x00, 0x08,
        0x00, 0x01,
        0x01, 0x12, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, 0x06, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
      ]);
      const merged = new Uint8Array(2 + exif.length + bytes.length - 2);
      merged.set([0xff, 0xd8], 0);
      merged.set(exif, 2);
      merged.set(bytes.subarray(2), 2 + exif.length);
      resolve(new Blob([merged], { type: "image/jpeg" }));
    }, "image/jpeg", 0.9);
  });
}

export function decoderAutoOrients(): Promise<boolean> {
  orientationProbe ??= (async () => {
    try {
      const probe = await buildOrientationProbe();
      if (!probe || typeof createImageBitmap !== "function") return false;
      const bitmap = await createImageBitmap(probe);
      const applied = bitmap.width === 1 && bitmap.height === 2;
      bitmap.close?.();
      return applied;
    } catch {
      // Assume the conservative case: we orient it ourselves.
      return false;
    }
  })();
  return orientationProbe;
}

async function decode(source: Blob): Promise<ImageBitmap | HTMLImageElement> {
  // `createImageBitmap` is the fast path and, importantly, throws on a corrupt
  // or unsupported image rather than silently producing a blank canvas.
  if (typeof createImageBitmap === "function") {
    try {
      return await createImageBitmap(source);
    } catch {
      // fall through to the <img> path
    }
  }
  const url = URL.createObjectURL(source);
  try {
    return await new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("decode failed"));
      image.src = url;
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

function encode(canvas: HTMLCanvasElement): Promise<ConvertedImage["blob"] | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), OUTPUT_MIME, OUTPUT_QUALITY);
  });
}

function encodeFallback(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), OUTPUT_FALLBACK_MIME, OUTPUT_QUALITY);
  });
}

/**
 * Decode, orient, downscale, and re-encode. Throws `ImageConversionError` with
 * a message meant for the user if any step fails.
 */
export async function prepareImageForUpload(file: File): Promise<ConvertedImage> {
  if (!file.size) {
    throw new ImageConversionError("That file is empty.");
  }

  let source: Blob = file;
  const heic = isHeic(file);

  if (heic) {
    try {
      // Dynamically imported: the libheif WASM payload is over a megabyte and
      // most users never pick a HEIC. Loading it eagerly would tax every visit
      // for a minority path.
      const { default: heic2any } = await import("heic2any");
      const converted = await heic2any({
        blob: file,
        toType: OUTPUT_FALLBACK_MIME,
        quality: OUTPUT_QUALITY,
      });
      source = Array.isArray(converted) ? converted[0] : converted;
    } catch {
      throw new ImageConversionError(
        "We could not read that HEIC photo. On your iPhone, try " +
          "Settings > Camera > Formats > Most Compatible, or share the photo " +
          "as a JPEG and upload that.",
      );
    }
  }

  // Orientation, resolved down to "what do we still have to do ourselves".
  //
  // HEIC is deliberately treated as already upright: libheif applies the
  // container's own rotation/mirror properties while decoding, and the JPEG it
  // hands back carries no EXIF block. There is nothing left for us to correct,
  // and `readExifOrientation` returns 1 for it anyway since it is not a JPEG.
  const declared = heic ? 1 : await readExifOrientation(source).catch(() => 1);
  // If the decoder auto-orients, the pixels arriving from `decode` are already
  // upright and applying the transform would rotate them a second time.
  const orientation =
    declared !== 1 && (await decoderAutoOrients()) ? 1 : declared;

  let decoded: ImageBitmap | HTMLImageElement;
  try {
    decoded = await decode(source);
  } catch {
    throw new ImageConversionError(
      "That image could not be opened. It may be damaged or in a format we " +
        "cannot display.",
    );
  }

  const rawWidth = "width" in decoded ? decoded.width : 0;
  const rawHeight = "height" in decoded ? decoded.height : 0;
  if (!rawWidth || !rawHeight) {
    throw new ImageConversionError("That image has no visible content.");
  }

  const swap = orientationSwapsAxes(orientation);
  const orientedWidth = swap ? rawHeight : rawWidth;
  const orientedHeight = swap ? rawWidth : rawHeight;
  const target = fitWithin(orientedWidth, orientedHeight);

  const canvas = document.createElement("canvas");
  canvas.width = target.width;
  canvas.height = target.height;
  const context = canvas.getContext("2d");
  if (!context) {
    throw new ImageConversionError("This browser cannot process images.");
  }

  // Apply the orientation in the *unscaled* oriented space, then scale, so the
  // transform maths stays independent of the downscale factor. The transform
  // takes the source's own dimensions; the swap is already reflected in the
  // canvas size and in `scale`.
  const [a, b, c, d, e, f] = orientationTransform(orientation, rawWidth, rawHeight);
  const scale = target.width / orientedWidth;
  context.setTransform(a * scale, b * scale, c * scale, d * scale, e * scale, f * scale);
  context.drawImage(decoded as CanvasImageSource, 0, 0);
  context.setTransform(1, 0, 0, 1, 0, 0);
  if ("close" in decoded) decoded.close();

  let blob = await encode(canvas);
  let extension: ConvertedImage["extension"] = "webp";
  if (!blob || blob.size === 0) {
    // Very old Safari cannot encode WebP from a canvas.
    blob = await encodeFallback(canvas);
    extension = "jpg";
  }
  if (!blob || blob.size === 0) {
    throw new ImageConversionError(
      "We could not save that photo in a displayable format.",
    );
  }

  return {
    blob,
    extension,
    width: target.width,
    height: target.height,
    converted: heic || blob.type !== file.type,
  };
}
