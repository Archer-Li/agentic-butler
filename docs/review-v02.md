# Architecture and security review, 2026-10-23

Reviewed the supplied architecture, security model, roadmap and all implementation files. Inputs used were the independently authored starter, generic product requirements and public vendor documentation. No employer resource was accessed or incorporated.

## Findings addressed

1. A steps-only hash did not bind approval to repository, session or expiry. The new canonical scope envelope includes proposal/event identity, event metadata, repository, session, visible title/explanation, creation/expiry, exact steps, preview executor, simulation effect, empty tool allowlist and zero agent token budget. Approval records the scope digest; execution recomputes it and checks the approved digest.
2. The executor injection interface could accept arbitrary implementations. Runtime now rejects anything other than the built-in preview executor. There is no live agent launch path.
3. Duplicate keys silently hid conflicting event payloads. Conflicting deliveries now raise a validation error without replacing the original event.
4. Hook metadata could expose prompts, transcripts or local paths. The adapter extracts three documented fields and stores a fixed summary plus caller-selected repository label. No path is dereferenced. Hook metadata is untrusted input, never authorization.
5. Optional model calls and notifications need consent and limits. Both require explicit CLI opt-in. Model calls reserve persistent capacity before network activity; notifications claim once before delivery. No automatic install, cloud endpoint, credential or shell execution is introduced.
6. CI now explicitly grants only `contents: read`.

## Trust model and limits

SQLite and Python modules are trusted local state, owned by the same OS user. Digests detect inconsistent or post-approval scope changes; they are not signatures and cannot defend against an attacker who can rewrite the database, approval hashes and code together. Repository/session labels do not authenticate the caller or bind an OS workspace identity. A forged hook can create a suggestion, never authorize execution.

The migration adds nullable envelope columns to v0.1 databases. Legacy pending or approved proposals fail closed at approval/execution; they cannot inherit v0.2 authorization. Existing data remains readable. Re-emit the event using a new delivery ID and review the new proposal. Legacy or corrupt proposals can be rejected or revoked without passing approval integrity checks. They still cannot be approved or executed.

Any real agent integration still requires authenticated identity/workspace binding, tool enforcement outside model text, cancellation, deadlines, budget accounting, capability controls, adversarial integration tests and a separately approved execution policy. No real execution or privileged action is enabled by this release.

Remaining validation: live coding-agent callback delivery, live local-model inference and graphical notification delivery. Hosted CI passed for dev1 on Python 3.10, 3.12 and 3.13. Unit/CLI tests use synthetic personal-project fixtures. A kind-only triage model and static plans are an intentionally narrow foundation, not complete dynamic orchestration.

The dev2 observation workflow adds native Codex delivery identities, exact workspace filtering, bounded stdin, safe snippet generation, reviewed-scope matching, revocation and transactional audit rows. A startup worker no longer mutates running jobs into interrupted state; manual recovery remains explicit. Detailed validation and limitations are in [codex-setup.md](codex-setup.md).
