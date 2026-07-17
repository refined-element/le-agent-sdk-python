# Changelog

All notable changes to `le-agent-sdk` are documented here.

This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries begin at 0.3.3; for earlier history see the
[commit log](https://github.com/refined-element/le-agent-sdk-python/commits/main).

## [0.3.3] - 2026-07-17

### Fixed

- Fixes signature verification silently passing when secp256k1 is unavailable — upgrade recommended.
  `NostrEvent.verify()` returned `True` for any event whose ID matched when the
  secp256k1 native library could not be imported. The event ID is a plain SHA-256
  over public fields, so it is attacker-computable and proves nothing about
  authenticity — forged capability advertisements and forged attestations under any
  pubkey were accepted. Verification now raises `Secp256k1UnavailableError` instead
  of passing. secp256k1 requires a native build and is not importable in some
  environments where installation otherwise appears to succeed, so this affected
  real deployments rather than only misconfigured ones.
- Fixes `pay_and_access()` ignoring `max_amount_sats`, and budget checks being
  skipped for invoices whose amount could not be read — both allowed payments
  above the configured limit; upgrade recommended.
  `pay_and_access()` never consulted the limit at all, so a client constructed with
  `max_amount_sats=100` would pay a 10,000,000-sat invoice. Separately, an invoice
  whose amount could not be determined was treated as "no limit applies" and paid.
  An amount that cannot be determined is now refused whenever a limit is configured.
- Fixes the BOLT-11 amount parser reading an amount from the invoice data part.
  The pattern was not anchored to the human-readable part, so an amountless (i.e.
  unbounded) invoice whose data happened to contain `<digits><multiplier>1` was
  reported as a small amount and passed the budget check. Amounts are now read only
  from the human-readable part, and rounded up rather than truncated so a budget
  check is never given an under-reported value.
- Incoming relay events are now signature-verified before use. `discover()`,
  `get_attestations()` and `listen_requests()` passed raw relay JSON straight into
  the models. Relay lists are caller-configurable and results are merged across
  relays, so a single malicious relay could inject events attributed to any pubkey.
  Events failing verification are dropped and logged; other relays' results are
  unaffected.
- The `User-Agent` sent by `L402ProducerClient` reported `0.1.0` on every release
  since 0.1.0. It now tracks the package version.

### Added

- `Secp256k1UnavailableError`, exported from the package root. Subclasses
  `RuntimeError`, so existing `except RuntimeError` handlers continue to work.

### Changed

- `pay_and_access()` accepts a `max_amount_sats` argument to override the
  instance-level limit for a single call, matching `access()`.

### Upgrade notes

- If secp256k1 is not importable in your environment, verification now raises where
  it previously returned `True`. Any code path that reads events from relays is
  affected. This is intentional: the previous result was not a weaker check, it was
  no check. Installing secp256k1 restores normal operation.
- Callers relying on unknown-amount invoices being paid while `max_amount_sats` is
  set will now see `ValueError`. Either set no limit (explicitly opting out of
  budget enforcement) or use invoices with an explicit amount.
