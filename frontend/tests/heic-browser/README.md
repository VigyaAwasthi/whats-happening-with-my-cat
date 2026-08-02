# HEIC end-to-end verification (real browser)

`lib/images.ts` has two halves. The decision logic — format detection, EXIF
parsing, orientation matrices, downscaling — is covered by
`tests/images.test.mjs` in Node. The other half cannot be:

- `heic2any` needs `window`, `indexedDB`, `navigator`, and `location`
- canvas decode/encode needs a real canvas
- **whether the decoder auto-applies EXIF orientation differs per engine**

That last one is not a detail. Chrome honours `imageOrientation: "none"` and
returns raw pixels; WebKit ignores the hint and always auto-orients. Verified
directly in Safari, where `"none"`, `"from-image"`, and the default all returned
identical already-rotated dimensions. Applying our own transform on top of an
already-oriented bitmap rotates it a second time and lands the photo 90° from
where it belongs — the classic "HEIC upload came out sideways" bug.

`lib/images.ts` therefore *probes* the decoder at runtime rather than assuming.
A Node shim would test the shim, not the engine, so this harness exists to run
the real module in a real browser.

## Running it

```bash
npm run test:heic
```

That bundles `lib/images.ts` with esbuild, serves this directory, and prints
the results. Open the printed URL; the page title becomes `HEIC OK` or
`HEIC FAILED`, and each check is listed pass/fail. Two converted images are
rendered at the bottom so orientation can be judged by eye:

- the converted HEIC, which must look like a normal photo
- a 400x200 image tagged EXIF orientation 6, which must come out 200x400 with
  the blue stripe on the **right** edge (it starts on the top edge; orientation
  6 is a 90° clockwise rotation)

## The fixture

`../fixtures/real-photo.heic` is a genuine HEIC — `ftypheic` brand, `mif1`
compatible, HEVC-coded — produced by `sips` from a macOS system HEIC asset. It
is not a renamed JPEG, and `tests/images.test.mjs` asserts that so it cannot
quietly become one.

**Caveat, stated plainly:** it is a real HEIC but it is not a photograph taken
on an iPhone. It exercises the same libheif decode path, but it does not carry
an iPhone's specific EXIF block, and it has no rotation property, so the
HEIC-container rotation path is asserted by construction rather than observed.
Before trusting this in production, upload one photo straight from an iPhone
camera roll — ideally one shot in portrait, which is where orientation bugs
surface — and confirm it renders upright on the photo wall.
