/**
 * Node-runnable coverage for the pure parts of the image pipeline.
 *
 * The pipeline's browser half — libheif decoding, canvas re-encoding, and the
 * EXIF auto-orientation probe — cannot be honestly tested here. `heic2any`
 * needs `window`, `indexedDB`, and a canvas; shimming those would test the
 * shim. That half is verified in a real browser by `tests/heic-browser/`, which
 * runs the actual module against a real HEIC file. See its README.
 *
 * What IS tested here is everything that decides *what* the browser half does:
 * format detection, EXIF parsing, the orientation transform matrices, and
 * downscaling. Those are where the logic errors live.
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Bundle the real module rather than duplicating its logic into the test.
const outDir = mkdtempSync(join(tmpdir(), "images-test-"));
const bundle = join(outDir, "images.mjs");
execFileSync(
  join(process.cwd(), "node_modules", ".bin", "esbuild"),
  ["lib/images.ts", "--bundle", "--format=esm", `--outfile=${bundle}`, "--loader:.ts=ts"],
  { stdio: "pipe" },
);
const {
  isHeic,
  readExifOrientation,
  orientationSwapsAxes,
  orientationTransform,
  fitWithin,
  ACCEPTED_UPLOAD_TYPES,
} = await import(bundle);

const file = (name, type, bytes = [0xff, 0xd8, 0xff, 0xe0]) =>
  new File([new Uint8Array(bytes)], name, { type });

test("HEIC is detected by MIME type and by extension", () => {
  assert.equal(isHeic(file("IMG_1.HEIC", "image/heic")), true);
  assert.equal(isHeic(file("IMG_2.heif", "image/heif")), true);
  // iOS share sheets and the Files app frequently supply an empty MIME type,
  // which is why the extension has to be consulted at all.
  assert.equal(isHeic(file("IMG_3.HEIC", "")), true);
  assert.equal(isHeic(file("IMG_4.heic", "application/octet-stream")), true);
  assert.equal(isHeic(file("photo.jpg", "image/jpeg")), false);
  assert.equal(isHeic(file("photo.png", "image/png")), false);
});

test("the accept list only offers formats the pipeline can render", () => {
  // HEIC is present because it is converted. Anything listed here that the
  // pipeline could not decode would recreate the original bug: a file the user
  // is invited to pick, that then cannot be displayed.
  for (const type of ["image/jpeg", "image/png", "image/webp", "image/heic"]) {
    assert.ok(ACCEPTED_UPLOAD_TYPES.includes(type), `${type} should be offered`);
  }
  for (const type of ["image/tiff", "image/x-icon", "application/pdf"]) {
    assert.ok(!ACCEPTED_UPLOAD_TYPES.includes(type), `${type} must not be offered`);
  }
});

/** Build a JPEG carrying a real EXIF APP1 block with the given orientation. */
function jpegWithOrientation(orientation, endian = "big") {
  const little = endian === "little";
  const u16 = (value) =>
    little ? [value & 0xff, (value >> 8) & 0xff] : [(value >> 8) & 0xff, value & 0xff];
  const u32 = (value) =>
    little
      ? [value & 0xff, (value >> 8) & 0xff, (value >> 16) & 0xff, (value >> 24) & 0xff]
      : [(value >> 24) & 0xff, (value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
  const body = [
    ...(little ? [0x49, 0x49] : [0x4d, 0x4d]),
    ...u16(0x2a),
    ...u32(8),
    ...u16(1),
    ...u16(0x0112),
    ...u16(3),
    ...u32(1),
    ...u16(orientation),
    0x00, 0x00,
    ...u32(0),
  ];
  const app1 = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...body];
  return new Blob([
    new Uint8Array([
      0xff, 0xd8,
      0xff, 0xe1, ...u16BE(app1.length + 2), ...app1,
      0xff, 0xd9,
    ]),
  ]);
}
function u16BE(value) {
  return [(value >> 8) & 0xff, value & 0xff];
}

test("EXIF orientation is read from both byte orders", async () => {
  for (const orientation of [1, 3, 6, 8]) {
    assert.equal(
      await readExifOrientation(jpegWithOrientation(orientation, "big")),
      orientation,
      `big-endian orientation ${orientation}`,
    );
    assert.equal(
      await readExifOrientation(jpegWithOrientation(orientation, "little")),
      orientation,
      `little-endian orientation ${orientation}`,
    );
  }
});

test("a file with no EXIF, or no JPEG header, defaults to upright", async () => {
  assert.equal(await readExifOrientation(new Blob([new Uint8Array([1, 2, 3, 4])])), 1);
  assert.equal(await readExifOrientation(new Blob([])), 1);
  // A HEIC is not a JPEG, so the parser must decline rather than misread bytes.
  const heicHeader = new Uint8Array([0, 0, 0, 0x24, 0x66, 0x74, 0x79, 0x70, 0x68, 0x65, 0x69, 0x63]);
  assert.equal(await readExifOrientation(new Blob([heicHeader])), 1);
});

test("an out-of-range orientation is clamped to upright", async () => {
  assert.equal(await readExifOrientation(jpegWithOrientation(0)), 1);
  assert.equal(await readExifOrientation(jpegWithOrientation(99)), 1);
});

test("only orientations 5-8 swap the axes", () => {
  for (const upright of [1, 2, 3, 4]) {
    assert.equal(orientationSwapsAxes(upright), false, `orientation ${upright}`);
  }
  for (const rotated of [5, 6, 7, 8]) {
    assert.equal(orientationSwapsAxes(rotated), true, `orientation ${rotated}`);
  }
});

test("each orientation transform maps the image corners into the canvas", () => {
  // The transform is correct when it lands all four source corners inside the
  // oriented canvas. A wrong matrix pushes content off-canvas, which is what a
  // sideways or clipped photo actually is.
  const w = 400;
  const h = 200;
  for (let orientation = 1; orientation <= 8; orientation += 1) {
    const swap = orientationSwapsAxes(orientation);
    // The transform takes the SOURCE dimensions; the canvas is the swapped one.
    const [a, b, c, d, e, f] = orientationTransform(orientation, w, h);
    const canvasW = swap ? h : w;
    const canvasH = swap ? w : h;
    for (const [x, y] of [[0, 0], [w, 0], [0, h], [w, h]]) {
      const px = a * x + c * y + e;
      const py = b * x + d * y + f;
      assert.ok(
        px >= -0.001 && px <= canvasW + 0.001 && py >= -0.001 && py <= canvasH + 0.001,
        `orientation ${orientation}: corner (${x},${y}) -> (${px},${py}) escapes ${canvasW}x${canvasH}`,
      );
    }
  }
});

test("orientation 1 is the identity transform", () => {
  assert.deepEqual(orientationTransform(1, 100, 50), [1, 0, 0, 1, 0, 0]);
});

test("downscaling preserves aspect ratio and never upscales", () => {
  assert.deepEqual(fitWithin(4032, 3024, 2048), { width: 2048, height: 1536 });
  assert.deepEqual(fitWithin(3024, 4032, 2048), { width: 1536, height: 2048 });
  // Already small enough: left exactly alone.
  assert.deepEqual(fitWithin(800, 600, 2048), { width: 800, height: 600 });
  assert.deepEqual(fitWithin(1, 1, 2048), { width: 1, height: 1 });
  // Extreme panorama must not collapse to a zero-height canvas.
  const thin = fitWithin(10000, 3, 2048);
  assert.ok(thin.height >= 1, "height must stay renderable");
});

test("the real HEIC fixture is a genuine HEIC, not a renamed JPEG", () => {
  // Guards the browser harness: if someone swaps in a renamed .jpg, the
  // end-to-end test silently stops proving anything about HEIC.
  const bytes = readFileSync(new URL("./fixtures/real-photo.heic", import.meta.url));
  assert.equal(bytes.subarray(4, 12).toString("ascii"), "ftypheic");
  assert.ok(bytes.length > 5000, "fixture should be a real image, not a stub");
});
