# Public spectator API

[API index](README.md) · [Watch Hall guide](../../game/public-online.md) · [Online authority](../online-authority.md)

The public namespace is a separate, signed-out, GET-only projection:

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/public/online/games` | Bounded live/completed online discovery |
| GET | `/api/public/online/games/{id}` | Public live state |
| GET | `/api/public/online/games/{id}/replay` | Public completed replay |

It never returns single-player/local records, private profiles, email, full birth
dates, session/CSRF data, moderation notes or analysis. Response fields are an
allowlist, not the authenticated DTO with hidden keys. Invalid/private IDs return
not found rather than revealing existence.

Public discovery is enabled by default on the shipped configuration; an operator
can disable it with `HNEFATAFL_PUBLIC_ONLINE_ENABLED=false`, which returns 404 for
the whole namespace. Requests are rate-limited per client with a bounded window and
use short public cache headers. Private responses remain non-cacheable. Client
identity is never trusted for authorization.

The Watch Hall uses the same public projection. CORS is not opened for third-party
origins, and mutations are not exposed. See
[configuration](../../installation/configuration.md) and
[online operations](../../administration/online-operations.md).
