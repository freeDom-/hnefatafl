# Contributor licensing proposal

[Licensing](LICENSING.md) · [Contributing](../CONTRIBUTING.md) ·
[Development](DEVELOPMENT.md)

**Status: deferred proposal only. No CLA is active, and no repository enforcement
setting has been changed.** The maintainer does not currently intend to
commercially exploit Hnefatafl. This proposal is not required for ordinary
noncommercial contributions and grants no additional rights now. It would require
explicit maintainer approval and legal review before any activation. This document
is not legal advice.

## Why this proposal is deferred

A Developer Certificate of Origin confirms that a contributor has the right to
submit a contribution and agrees to the project's inbound license. It does not
grant broader rights where a separate future agreement might be considered. No
such broader grant is needed for the current noncommercial project, so ordinary
contributions do not need a CLA, DCO enforcement or an additional sign-off. This
proposal is retained only as a documented contingency if the maintainer ever
decides to seek broader rights.

## Current state

- No CLA, copyright assignment, DCO enforcement or sign-off requirement exists.
- Contributors retain copyright in their own contributions unless they
  separately agree otherwise.
- Contributions are inbound under the PolyForm Noncommercial License 1.0.0 only.
- External commercial exploitation of protected project material remains
  restricted by that license.
- Ordinary noncommercial contributions do not require any additional legal
  agreement.

## Deferred option: Harmony Individual CLA 1.0, Option Five

If the maintainer ever explicitly decides to seek broader rights, the documented
option is the established
[Harmony Individual Contributor License Agreement 1.0](https://harmonyagreements.org/agreements/)
rather than drafting a new agreement. It is designed for independent projects,
does not transfer copyright ownership, and keeps the contributor's own rights to
use and license the contribution.

Contingent configuration:

- **Agreement:** Harmony Individual Contributor License Agreement 1.0.
- **Outbound option:** Option Five would permit licensing the contribution under
  any license, including commercial or proprietary terms, while also requiring
  the contribution to remain available under the license used for the project on
  the submission date. This is a deferred contingency, not a current strategy.
- **Media:** The same inbound/outbound licensing treatment for contributed
  artwork, documentation and other non-software media, unless a contributor
  explicitly contributes under a different compatible license.
- **Patent grant:** Keep the Harmony patent license. It protects the project and
  downstream users without requiring an ownership transfer.
- **No assignment:** Use the license agreement, not the Harmony Contributor
  Assignment Agreement. Contributors retain copyright.
- **Entity contributions:** Use the matching Harmony Entity CLA for work
  contributed on behalf of an employer or other legal entity, or require the
  individual to confirm employer authorization as the agreement requires.
- **Attribution:** Preserve the Harmony template's CC BY 3.0 attribution and do
  not silently rewrite its legal terms. Fill only the project name, submission
  instructions, jurisdiction, optional affiliations and media-license list after
  legal review.

The Harmony template contains placeholders for the project name, submission
instructions, jurisdiction and optional media licenses. The owner, with legal
advice if desired, must complete those placeholders before publication. Do not
commit an unsigned draft as if it were an active agreement.

## Hypothetical activation plan after explicit owner approval

No activation is planned. If the maintainer later approves activation:

1. Record the approved agreement at a stable path, for example
   `docs/legal/CLA-INDIVIDUAL.md`, with a version identifier and effective date.
   Keep the canonical Harmony attribution.
2. Add a matching entity agreement if the project will accept corporate
   contributions.
3. State in `CONTRIBUTING.md` that the CLA is active, how to sign it, and which
   contributions it covers. Remove the current "no active CLA" wording at that
   point only.
4. Configure a CLA check that requires a signature before a pull request can be
   merged. Do not activate the check in the same change that introduces this
   proposal.
5. Test the flow with a non-production fork or a draft pull request before
   enabling required status checks on `main`.
6. Preserve the signatures and the exact agreement version. A later agreement
   change needs a new version and a re-signature path; do not silently apply new
   terms to an earlier contribution.

## GitHub automation options

Either option below is suitable; choose one after owner review. Pin any action to
a reviewed commit SHA rather than a floating tag.

### Option A: Contributor Assistant GitHub Action

- Repository: <https://github.com/contributor-assistant/github-action>
- Runs in GitHub Actions and stores signatures in a JSON file on a dedicated
  branch or repository path configured by the owner.
- Works without installing a GitHub App and fits a repository that already uses
  Actions.
- Needs a workflow with `pull_request_target` or equivalent event permissions;
  review the security implications carefully and do not expose secrets to
  untrusted pull-request code.

### Option B: CLA Assistant hosted app

- Repository: <https://github.com/cla-assistant/cla-assistant>
- Hosted service with a GitHub App installation and comment-based signing.
- Less repository workflow code, but introduces an external service and a
  dependency on its availability and data handling.
- Review its privacy, retention and permissions before installing it.

For either option, the check should cover new contributors and changes to an
existing agreement version. Maintainers, bots and already-covered contributors
need an explicit, documented allowlist. Do not add a wildcard allowlist that
quietly bypasses the check.

## Rejected alternatives

- **Copyright assignment:** Unnecessary, contributor-hostile for a community
  project, and outside the maintainer's stated preference.
- **DCO only:** Confirms provenance but does not grant broader rights if a future
  separate agreement were ever considered. It remains sufficient for ordinary
  contributions under the current model.
- **Custom CLA written from scratch:** Adds legal risk and review cost when an
  established template already matches the intended grant.
- **Automatic commercial licensing program:** The project explicitly does not
  offer one. If a deferred CLA were ever activated, it would grant the maintainer
  rights; it would not create a public commercial entitlement.

## Open decisions for the maintainer

The current decision is to keep this proposal deferred. If activation is ever
considered, the maintainer would still need to:

- Approve the exact agreement version and Option Five outbound terms.
- Choose the governing jurisdiction and complete the submission instructions.
- Decide whether entity contributions are accepted.
- Choose Action-based or hosted-app signing, or a manual signed-agreement
  process for a small contributor base.
- Obtain legal review before activation.
