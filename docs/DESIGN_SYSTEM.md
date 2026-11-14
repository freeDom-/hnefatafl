# Arena v2 design system

[Documentation index](README.md) · [Personalization](PERSONALIZATION.md) ·
[Arena contracts](designs/arena-v2-contracts.md)

The four supported UI themes share one information architecture. King’s Table uses
parchment, pine and gold; Iron & Ash uses charcoal stone, forged metal and restrained
gold; Saga Hall uses illustrated timber architecture, raven carvings, leather and
bronze; Classic Table uses flat parchment and forest green. The approved handoff
reference images guide composition, materials and hierarchy; live content remains
server-owned HTML and React, with no text, controls or game state baked into art.

## Navigation and layout

At desktop widths (1024 CSS px and above), every Arena route retains the persistent
sidebar and slim top bar, including Play, account games, local 1v1, online games and
replay. Workshop shares the shell while retaining its permission boundary. Phones
use the compact header and bottom navigation; tablets retain their existing header
navigation. Short coarse-pointer landscape devices retain the compact treatment.
Never replace desktop gameplay navigation with the old horizontal navbar.

The theme label sits immediately left of its select, vertically centred in both
English and German. Language stays beside the account menu. Skip links, route focus,
keyboard controls, replay controls and visible focus indicators remain functional.

Dashboard prioritizes introduction/status, hero and real board, selected profile,
four standing cards, bounded game previews, compact leaderboard and recent records.
The primary fixture composition fits at 1920×1080/Default and 3840×2160/Large.
Genuinely longer queues and records retain normal vertical scrolling. Grid tracks,
fluid board sizing and shared spacing tokens preserve hierarchy without clipping.
See [scale defaults and overrides](PERSONALIZATION.md#automatic-and-explicit-scale).

## Materials and safe assets

The local allowlist `frontend/src/arenaAssets.ts` supplies decorative paths. Never
construct artwork URLs from account names or IDs. Portraits continue using their
server-approved catalog selection. Missing decorative art falls back to solid
palette surfaces. Artwork and trim cannot receive input or cover focus indicators.

New original backgrounds were generated with the built-in imagegen tool, inspected
and converted to WebP without upscaling their 1672×941 sources:

| Asset | Dimensions | Intended use |
| --- | --- | --- |
| saga-hall-great-hall.webp | 1672×941 | Dark carved hall, ravens and warm edge firelight |
| saga-hall-great-hall-small.webp | 1280×720 | Same scene for CSS viewports up to 1400px |
| iron-ash-forge.webp | 1672×941 | Cool stone/steel architecture with restrained embers |
| iron-ash-forge-small.webp | 1280×720 | Smaller viewport variant |
| carved-frame.svg | 120×120, scalable | Reusable bronze/carved corner framing for panels, portrait and controls |

Source prompts describe original empty architectural scenes, with detail concentrated
at the edges and a quiet dark centre, no people, words, UI, boards or watermarks.
Saga Hall calls for carved timber, raven pillars, leather banners and bronze under
warm firelight. Iron & Ash calls for slate arches, forged steel, cool shadows and
small warm edge lights. These project-created assets contain no third-party artwork.

Reuse the original forest rail, raven/leather panel, board grain, knot-band SVG,
existing material textures and approved portrait catalog. Background scenes stay
static and responsive; individual panels retain controlled material detail. Saga
buttons remain accessible HTML with bronze borders, readable gradients, hover,
active, disabled and focus-visible states. Reduced motion removes transitions.

## Acceptance evidence

The browser matrix in `frontend/tests/arena-final.spec.ts` captures empty/populated
Dashboard, Play, single-player, local, online, replay, Statistics, authorized Workshop
and appearance settings for all themes at both target sizes. It asserts real label
geometry, square boards, persistent navigation, no horizontal overflow, primary
composition fit, automatic scale, explicit overrides and genuine 200% Chromium zoom.
The zoom extension is test-only and is never shipped with the application.

See [visual acceptance record](validation/arena-final-acceptance.md).

## Original artwork prompts

The built-in imagegen tool produced both source scenes. The generated PNGs were
inspected, then encoded as WebP at quality 83 without upscaling. Runtime assets
are saved in `frontend/public/assets/arena-v2/assets/art/`; the scalable frame is
in `frontend/public/assets/arena-v2/assets/icons/`. One selected scene costs about
180 KB (Saga Hall) or 158 KB (Iron & Ash); smaller variants are about 111 KB and
94 KB. The native source dimensions are documented above, rather than being
represented as native 4K artwork.

Saga Hall prompt:

> Use case: stylized-concept. Asset type: original decorative website background artwork, wide landscape 16:9, ideally 3840 x 2160. Illustrate a richly atmospheric ancient Norse great hall at night, dark carved timber pillars with subtle raven and intertwined Nordic knot carvings, leather banners and bronze fixtures, warm amber firelight from hearths at the side edges, deep spacious architectural perspective and smoky rafters. Clear, crisply painted detailed surfaces, controlled cinematic warmth. The large central 70% should be dark and relatively quiet to support opaque application panels, with the most distinctive craftsmanship at the left and right margins and upper edge. No people, no words, no letters, no runes resembling text, no UI, no board, no logos or watermark. This is a complete architectural illustration, not a repeating texture. Original fantasy historical concept art, cohesive palette charcoal timber, oxblood leather and antique bronze, premium game-art quality.

Iron & Ash prompt:

> Use case: stylized-concept. Asset type: original decorative website background artwork, very wide landscape 16:9 ideally 3840 x 2160. An atmospheric empty ancient stone and forged-steel war chamber, slate architectural pillars and arches receding into depth, cool charcoal and gunmetal surfaces with fine chisel marks, a restrained glint of warm gold and ember light at far side edges, cinematic crisp side lighting and cool blue-grey shadows. Premium realistic painted concept art for the Iron & Ash strategy game interface. Most architectural detail belongs at far left and right margins and upper edge; keep central 70% dark and quiet for application panels. Clearly distinguish from a wooden Norse great hall: stone architecture, refined forged metal, no brown wood, no ornate gold flourishes. No UI, text, letters, people, board or watermarks. This is a full architectural scene with visual depth, not a repeated slate texture. Avoid blur, muddy fog or an opacity wash.
