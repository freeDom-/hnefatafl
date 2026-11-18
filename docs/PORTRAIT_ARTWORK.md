# Portrait gallery artwork provenance

[Progression](PROGRESSION.md) · [Design system](DESIGN_SYSTEM.md)

The repository contained only 320 × 320 derivatives; no higher-resolution original
sources were available. The twelve `*-large.webp` files in
`frontend/public/assets/arena-v2/assets/portraits/` are new identity-guided redraws,
produced with the built-in image generation tool using each existing portrait as
its reference. They preserve the established Viking character, pose, proportions,
wardrobe and dark painterly setting. They are replacements for gallery display,
not recovered originals or interpolated enlargements.

The prompt set used this instruction for each allowlisted character:

> Redraw this Viking portrait at high native resolution. Preserve the character's
> identity, face, pose, composition, costume, palette and established realistic
> painterly Viking style. Render crisp, natural facial, hair, fur and metal detail.
> Square portrait, no text, no border, no extra characters or objects.

Shieldmaiden and Skald received a final reference-guided correction removing
unrequested birds from their hair while preserving the person, hairstyle, wreath,
lighting and composition. All outputs were inspected together before encoding.

[The source manifest](portrait-gallery-artwork.json) records each generated source
filename, native dimensions, final dimensions, byte size and WebP quality. Native
outputs were at least 1024px square and downsampled to 1024 × 1024, encoded WebP at
quality 82, method 6. The twelve assets total 2,857,036 bytes. No sharpening or
upscaling is applied. The original 320px assets remain byte-for-byte unchanged.

`Portrait.tsx` derives the gallery mapping from the existing local ID allowlist.
Only the Hall requests the lazy-loaded large assets; other avatar contexts retain
the originals. Gallery sizing uses actual asset width divided by device pixel
ratio, capped by card space (up to 440 CSS pixels). Portraits remain centered and
proportional inside independently sized frames. Unknown IDs fall back to the
default; a failed image load displays the existing rune fallback.

See [visual validation](validation/automatic-achievements.md) for rendered evidence.
