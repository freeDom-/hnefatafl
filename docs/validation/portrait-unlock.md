# Manual portrait unlock validation

[Progression](../PROGRESSION.md) · [Design system](../DESIGN_SYSTEM.md)

Shieldmaiden in the Hall of Renown at 1920 × 1080 with King’s Table. Read left to
right, top to bottom for ready, first reveal at 320 ms, expansion at 1.30 s and
celebration at 2.66 s.

![Shieldmaiden in grayscale, with the first irregular color patch, with merging color patches, and fully colored with a golden frame and floating runes](../screenshots/portrait-unlock/shieldmaiden-reveal.webp)

The comparison used the bundled 1024 × 1024 Shieldmaiden in both the supplied
prototype and the Hall. The seeded curves, start locations, delays and expansion
timing follow the prototype. The Hall feathers the union of its masks at a maximum
of 480 px. The temporary color layer caps at 2× device scale and 640 px to bound
work on high-DPI screens. After the masks cover the full portrait, the Hall
restores the original image at its normal display resolution. It uses the existing
gallery frame and positions the runes around its edge within the card spacing.

## Regression checks

- `tests/test_progression.py` covers automatic milestones below, at and above
  the unlock threshold, own-account authenticated claims, CSRF and origin checks,
  duplicate requests, concurrent sessions, result corrections, remounting the
  application and upgrading historical entitlement and selection data.
- `frontend/tests/portrait-unlock.spec.ts` covers visible art coverage, complete
  restoration, failed and lost responses, retries, reduced motion, missing image
  and Canvas 2D data, simultaneous reveals, backgrounding and route cleanup. An
  unmocked browser flow claims with the real API and equips during the animation.
- `frontend/tests/portraits.spec.ts` exercises all four themes in English and
  German on desktop and emulated mobile. It checks keyboard focus, artwork
  geometry, device-pixel limits, UI scale settings and horizontal overflow.

Playwright writes the Chromium capture frames to its results directory.

```sh
PORTRAIT_UNLOCK_EVIDENCE=1 npm --prefix frontend run test:e2e -- tests/portrait-unlock.spec.ts --project=chromium --grep 'reveals localized'
```

Chromium mobile emulation covers layout and interactions. Physical-device
performance and Safari rendering remain unmeasured.
