# Changelog

All notable changes to `le-agent-sdk` are documented here.

This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries begin at 0.4.0; for earlier history see the
[commit log](https://github.com/refined-element/le-agent-sdk-python/commits/main).

## [0.4.0] - 2026-07-17

Security release. Fixes signature verification silently passing, two payment-budget
bypasses, and unverified relay events — **upgrading is recommended**.

This release also replaces the package's crypto dependency, which is why it is a
minor bump rather than a patch: see [Dependencies](#dependencies) below.

### Dependencies

- **Replaces the `secp256k1` dependency with `coincurve`, which ships prebuilt
  wheels.** `secp256k1` required a native build (libsecp256k1 plus a C toolchain)
  and failed to install on Windows entirely; even where a build was possible it
  frequently produced an install where `import secp256k1` failed, which is what the
  verification bug below turned into a silent security hole. `coincurve` provides
  the same BIP-340 Schnorr primitives over the same curve with no build step.

  For most users this is transparent — `pip install le-agent-sdk` simply starts
  working where it previously did not. Two things to be aware of:

  - If your project imported `secp256k1` itself and relied on this package to pull
    it in, it is no longer installed transitively. Declare it directly.
  - The signature wire format is unchanged. Events signed by 0.3.x verify under
    0.4.0 and vice versa; the curve, key encoding, and BIP-340 semantics are
    identical, only the binding differs. This is covered by cross-implementation
    tests against the .NET SDK and the BIP-340 published vectors.

### Fixed

- Fixes signature verification silently passing when the crypto backend is
  unavailable — upgrade recommended.
  `NostrEvent.verify()` returned `True` for any event whose ID matched when the
  native secp256k1 library could not be imported. The event ID is a plain SHA-256
  over public fields, so it is attacker-computable and proves nothing about
  authenticity — forged capability advertisements and forged attestations under any
  pubkey were accepted. Verification now raises `CryptoBackendUnavailableError`
  instead of passing. Because the old dependency could not be installed at all on
  some platforms, this affected real deployments rather than only misconfigured
  ones — and the dependency swap above removes the condition for nearly all of them.
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

- `CryptoBackendUnavailableError`, exported from the package root. Subclasses
  `RuntimeError`, so existing `except RuntimeError` handlers continue to work.
  `Secp256k1UnavailableError` is kept as an alias of it.
- Cross-implementation wire-compatibility tests: events signed by the .NET SDK
  (via NBitcoin.Secp256k1) are committed as fixtures and verified on every run,
  alongside the BIP-340 published test vectors.

### Changed

- `pay_and_access()` accepts a `max_amount_sats` argument to override the
  instance-level limit for a single call, matching `access()`.

### Upgrade notes

- `pip install le-agent-sdk` no longer needs a C toolchain. If you previously
  installed build dependencies (libsecp256k1, build-essential, Visual C++ Build
  Tools) solely for this package, they are no longer required.
- If the crypto backend is not importable in your environment, verification now
  raises where it previously returned `True`. Any code path that reads events from
  relays is affected. This is intentional: the previous result was not a weaker
  check, it was no check.
- Callers relying on unknown-amount invoices being paid while `max_amount_sats` is
  set will now see `ValueError`. Either set no limit (explicitly opting out of
  budget enforcement) or use invoices with an explicit amount.

### Note on 0.3.3

An earlier cut of this work was staged as 0.3.3 and was never published to PyPI.
Its contents are released here as 0.4.0; no 0.3.3 artifact exists. The
`Secp256k1UnavailableError` name originated in that unreleased cut, so no released
version ever exported it — it is aliased anyway for anyone tracking the branch.
