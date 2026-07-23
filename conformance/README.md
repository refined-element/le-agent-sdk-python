# Port-drift conformance check

This directory is the **cross-port conformance suite** for the three Lightning
Enable Agent SDK ports:

- Python — `le-agent-sdk` (`F:\le-agent-sdk-python`, default branch `master`)
- .NET — `LightningEnable.AgentSdk` (`F:\le-agent-sdk-dotnet`, default branch `main`)
- TypeScript — `le-agent-sdk` (`F:\le-agent-sdk-ts`, default branch `main`)

## Why this exists (the oracle)

An audit of the three ports found that **wherever the ports of a shared behavior
disagreed, at least two of the three were wrong.** This held on every divergence
found (auth bypass, preimage fabrication, `max_amount` gap, ratings filter, the
`#41` price-tag divergence, the `#61` negotiable-floor divergence).

That observation is turned into a CI-enforced check here: for each
security-critical shared behavior we define **golden vectors** (input -> expected
output) in language-neutral JSON, and each port ships a **conformance test** that
runs those same vectors through *its own* implementation. If any port diverges,
that port's own CI goes red. Drift is caught automatically instead of by manual
cross-reading.

## What is covered (first version)

| Vectors file | Behavior | Entry point |
|---|---|---|
| `vectors/price-tag.json` | Parsing a capability `price` tag amount | `AgentCapability.fromNostrEvent` / `from_nostr_event` / `FromNostrEvent` |
| `vectors/negotiable-floor.json` | Parsing the `["negotiable","floor","<amount>"]` branch | same |
| `vectors/discover-resilience.json` | `discover()` batch resilience to one malformed payload (ledger #41) | `AgentManager.discover` + each port's relay-ingest layer |

Each vectors file is self-describing: it names the behavior, the entry point, the
outcome vocabulary, and every vector's expected outcome.

### Findings this suite encodes

- **Price parsing agrees across all three ports.** Valid amounts parse; `abc`,
  `10.5`, `100abc` are rejected (throw); a bare `["price"]` records no price; and
  `0` is valid (a free service). A **negative** amount (`-5`) was originally
  **accepted by all three** and flagged in `price-tag.json` as an open
  `designQuestion`. That question is now **decided (ledger #69, 2026-07-22):
  a negative price/floor is rejected** — it is never meaningful and accepting it
  is a fail-open smell, so it is treated like any other malformed amount (throw ->
  the event is skipped). The golden now REJECTS `-5` (`negative-amount-rejected`)
  and pins `0` as valid (`zero-accepted`); all three ports were tightened to
  conform.
- **Negotiable-floor did NOT agree.** Python and .NET reject a malformed floor
  amount (throw -> the event is skipped). TypeScript used `parseInt()`, which
  returns `NaN` for `"abc"` and silently truncates `"10.5"`->`10` /
  `"100abc"`->`100`, and never throws. Two ports reject, one keeps a bogus value:
  by the oracle, the one that keeps it is the bug (ledger #61). The golden REJECTS
  a malformed floor and the TypeScript port was fixed to conform (a `NaN` floor is
  worse than useless: every price-floor comparison against `NaN` is false, so a
  malformed floor silently passes downstream instead of being rejected). The floor
  amount is parsed with the **same** non-negative-integer rules as the price
  amount, so a **negative** floor is rejected too (ledger #69; golden
  `negative-floor-rejected`, with `0` pinned valid by `zero-floor-accepted`).

## How each port wires it into CI

The conformance test is an ordinary test file, so each repo's existing test job
picks it up with no workflow change:

- **Python** — `tests/test_conformance.py`, run by `pytest tests/` (`.github/workflows/test.yml`).
- **.NET** — `tests/LightningEnable.AgentSdk.Tests/ConformanceTests.cs`, run by
  `dotnet test`. The vectors are linked into the test project and copied next to
  the test DLL (`<None Include="..\..\conformance\vectors\*.json" CopyToOutputDirectory="PreserveNewest" />`).
- **TypeScript** — `tests/conformance.test.ts`, run by `vitest run` (`npm test`).

Each port reads the JSON from this canonical directory (`conformance/vectors/`) at
the repo root, so the test and the vectors can never point at stale copies.

## How the vectors stay in sync across the three repos

These are three separate repositories (no monorepo), so the vectors are
**physically copied** into each one at the same path. Two things keep the copies
honest:

1. **A single source of truth.** The canonical copy lives in the **Python** repo
   (`le-agent-sdk-python/conformance/`). To change a vector, edit it there, then
   copy `conformance/vectors/*.json` verbatim into the other two repos in the same
   change set.
2. **A shared checksum guard.** `conformance/CHECKSUMS` lists the SHA-256 of each
   vectors file and is **byte-identical in all three repos**. Every port's
   conformance test recomputes the checksums of its local vectors (over
   **LF-normalized** bytes, so CRLF checkouts on Windows CI don't matter) and
   asserts they match `CHECKSUMS`. Because the same `CHECKSUMS` constant is present
   in every repo and each repo's JSON must match it, the JSON is transitively
   identical across all three. If someone edits a vector in one repo only, that
   repo's checksum test fails; if they also update `CHECKSUMS` but forget a repo,
   the forgotten repo fails. Either way CI catches the drift.

Regenerate `CHECKSUMS` after changing any vector:

```sh
# from the repo root, on any port
cd conformance
python - <<'PY'
import hashlib, pathlib
lines = []
for p in sorted(pathlib.Path("vectors").glob("*.json")):
    data = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    lines.append(f"{hashlib.sha256(data).hexdigest()}  {p.name}")
pathlib.Path("CHECKSUMS").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
PY
```

## How to extend

**Add a behavior to this suite:**

1. Add a `vectors/<behavior>.json` file (same self-describing shape: `behavior`,
   `entrypoint`, an outcome vocabulary, and a `vectors`/`scenarios` array).
2. First DISCOVER what each port actually does for every vector (read the code /
   run it). Where all three agree, that agreed output is the golden. Where they
   diverge: if the correct behavior is unambiguous, set the golden to it and FIX
   the outlier port (failing-vector-first: make it red, fix, green); if it is a
   real design question, set the golden to the current agreement and surface the
   question rather than picking a side.
3. Add an assertion block for the new file to each port's conformance test.
4. Regenerate `CHECKSUMS` and copy the new vectors + `CHECKSUMS` into all three
   repos in one change set.

**Extend to the `l402-*` three-port trio:** the same pattern transplants directly.
The `l402-client-*` libraries share security-critical parsing too — bolt11 invoice
amount decoding, `max_amount` budget enforcement, the L402 `WWW-Authenticate`
challenge parse. Stand up a parallel `conformance/` in the `l402-*` repos with
`vectors/invoice-amount.json`, `vectors/max-amount-budget.json`,
`vectors/l402-challenge.json`, and the identical CHECKSUMS-guard + single-source
copy mechanism. Candidate next behaviors already visible in these SDK ports (all
still using bare `parseInt`/`int()` and therefore worth pinning): budget-tag,
attestation-rating, and request-expiration parsing.
