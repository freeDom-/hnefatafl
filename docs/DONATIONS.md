# Voluntary donations

[Repository introduction](../README.md) · [Licensing](./LICENSING.md) ·
[Contributing](../CONTRIBUTING.md)

Hnefatafl accepts completely voluntary financial support through Buy Me a
Coffee:

<https://buymeacoffee.com/free.dom>

The GitHub funding configuration points to the same account:

```yaml
buy_me_a_coffee: free.dom
```

## Donation policy

- **Donations are voluntary.** Nobody is required to donate to use, self-host or
  contribute to Hnefatafl.
- **Donations are not software license purchases.** A donation does not grant a
  license, expand the rights granted by the
  [PolyForm Noncommercial License 1.0.0](../LICENSE), or authorize commercial
  use. Commercial use requires separate explicit authorization from the relevant
  rights holders.
- **Hnefatafl remains free regardless of donations.** Private, educational,
  charitable and other permitted noncommercial use is available under the project
  license whether or not anyone donates.
- **All gameplay and AI features remain free.** There are no paid features,
  paid tiers, donor-only builds, subscriptions or commercial licensing products.
- **Donations unlock no features or privileges.** Donations do not add features
  or privileges to any part of the application.
- **Donors gain no gameplay, ranking, matchmaking or training advantages.**
  Donations do not affect online eligibility, ratings, achievements, portraits,
  tournaments, model access, training priority or any other application behavior.
- **Non-donors have identical access.** The application does not know or care
  whether a visitor donated.
- **Donations do not imply tax-deductible charitable status.** The project does
  not represent itself as a charity and makes no tax-deduction claim. Donors are
  responsible for their own tax treatment.

## How the integration works

- The support link opens the external Buy Me a Coffee page in a new tab with
  `rel="noopener noreferrer"`.
- The frontend contains only a plain outbound link. It has no embedded widget,
  payment script, tracking pixel, donation popup or reminder.
- There is no backend donation endpoint, webhook, database field, donor account
  flag, entitlement check or payment processing in Hnefatafl.
- Buy Me a Coffee processes any payment and handles the associated account and
  privacy terms. The project does not receive or store payment-card data.
- The support destination is centralized in
  `frontend/src/supportLinks.ts` for the frontend link and mirrored in
  `.github/FUNDING.yml` and this documentation.
- The donation integration introduces no runtime dependency and no additional
  network request unless a visitor deliberately activates the link.

## Relationship to licensing and contributions

Donations do not change the project license, contributor terms or commercial
authorization process. They also do not fund or imply any right to influence
project decisions, obtain priority support or receive a commercial license.
