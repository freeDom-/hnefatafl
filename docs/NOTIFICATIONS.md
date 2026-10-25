# Frontend notifications

[Documentation index](README.md) · [Architecture](../ARCHITECTURE.md) · [Development](DEVELOPMENT.md)

User-facing transient messages use one shared notification layer instead of
per-page error banners. The implementation lives in
[frontend/src/notifications.tsx](../frontend/src/notifications.tsx).

## Shared API

The module owns a small external store and exposes:

- `notify.error(message, options?)`, `notify.success(...)`, `notify.info(...)`
- `dismissNotification(id)` and `clearNotifications()`
- `useNotifications()` for components that need the current list
- `NotificationProvider`, which renders the single viewport at the application
  root (`frontend/src/main.tsx`) and renders children unchanged

Errors stay until dismissed. Success and informational messages auto-dismiss
after six seconds. Identical messages are deduplicated for five seconds and at
most three notifications are visible at once, with further messages queued and
bounded to five entries.

`api()` in [frontend/src/api.ts](../frontend/src/api.ts) reduces server payloads
to a readable string, caps the length and falls back to a generic message. The
shared `errorMessage(error, fallback?)` helper provides the same guarantee for
non-API failures. Stack traces, objects and empty messages are never rendered.

## Rendering and accessibility

The viewport is fixed to the top centre of the viewport and uses a high stacking
order so it is never clipped by a scroll container. Native modal `<dialog>`
elements paint above every ordinary fixed layer, so the viewport is portaled into
the topmost open dialog while one exists and back to `document.body` otherwise.
Each message uses `role="alert"` (errors) or `role="status"` (success/info),
ships an accessible close button, and never moves focus. Long messages wrap
inside the viewport on both desktop and mobile.

## Migrated surfaces

- The former top-of-page application error banner.
- New-game creation failures (selections and retry are preserved).
- Login, logout and expired-session errors.
- Model rename, training resume, tournament creation, remote-worker
  create/revoke/override/copy, and analytics-settings save failures.
- Errors reported from the hall of records, tournaments and training workshop
  through their `onError` callbacks.

## Intentional exceptions

Some states stay inline because they are contextual rather than transient:

- Field validation, for example invalid training numbers, incompatible models
  and unsupported inference/symmetry targets.
- Persistent page or dialog states such as "training jobs could not be loaded",
  "no resumable checkpoint", recorded job failures and a model-usage check that
  disables deletion until the user retries.
- The registration-unavailable notice in the login screen, which is
  informational and persistent.
- Console-only diagnostics and expected cancellations are not surfaced.

A failed form submission may show a concise snackbar summary while the form
keeps its field-level guidance.

## Tests

`frontend/src/notifications.test.tsx` covers dismissal, timeout, stacking,
deduplication, fallback text, live-region roles and focus stability.
`frontend/src/GameSetupPreference.test.tsx` covers the new-game failure and
retry path. `frontend/tests/notifications.spec.ts` is the Playwright regression
for the original scrolled-dialog visibility issue on desktop and mobile.
