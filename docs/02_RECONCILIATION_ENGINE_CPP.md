# 02 — Reconciliation Engine (C++)

**Owner:** Agent B
**Owns:** `recon/src/*.cpp`, `recon/include/*.hpp`, `recon/tests/*`
**Depends on:** `05_DATABASE_AND_EVENT_SCHEMA.md` (`reconciliation_exceptions` table, event schema)
**Does not touch:** ledger core, gateway, RAG service

---

## 1. Responsibility

This is a batch, throughput-bound job — deliberately **not** a request/response service, because that's how real reconciliation actually runs (scheduled, end-of-day, or on settlement-file arrival). It performs a **three-way match**:

| Source | What it represents |
|---|---|
| Internal ledger (`transactions` + `ledger_entries` via Postgres, read-only) | What we *think* happened |
| Payment gateway record (API-fetched or file-provided) | What the PG *thinks* happened |
| Bank settlement file (fixed-width or CSV, T+1/T+2) | What the bank *actually* settled |

Any row that doesn't match cleanly across all three becomes a `reconciliation_exceptions` row — never silently dropped or auto-corrected.

## 2. Why C++ for this piece specifically

- Settlement files from real institutions are still frequently **fixed-width mainframe-derived formats** — this is a genuine, current pain point in BFSI integration work, and parsing them fast and correctly (including EBCDIC/packed-decimal edge cases if you want to go further) is a C++-shaped problem.
- The match itself is an **embarrassingly parallel, CPU-bound batch job** over potentially millions of rows — ideal for tight C++ with OpenMP/std::execution parallel algorithms, not a good fit for Python's GIL.
- This demonstrates the "legacy interoperability" skill BFSI backend teams specifically screen for, distinct from the Rust core's "new system correctness" skill.

## 3. Tech stack

- C++20, `CMake` build
- `libpqxx` for read-only Postgres access
- `std::execution::par_unseq` (or OpenMP if your compiler support is limited) for parallel matching
- A tiny embedded rule engine (see §5) so match tolerances are configurable without recompiling
- Exposed to the rest of the system via a **thin gRPC or CLI + file-drop interface** — Python (doc 03/07) invokes it as a subprocess or long-lived gRPC service and reads its output rows, rather than the C++ code talking to Kafka directly (keeps the C++ surface area small and testable).

## 4. Core data structures

```cpp
// recon/include/recon/types.hpp
#pragma once
#include <cstdint>
#include <string>
#include <optional>

namespace recon {

enum class Source { LEDGER, PAYMENT_GATEWAY, BANK_FILE };

struct SettlementRecord {
    std::string external_reference;   // matches transactions.reference_id
    int64_t     amount_minor;
    std::string currency;
    std::string status;               // "SUCCESS" | "FAILED" | "PENDING"
    Source      source;
};

enum class ExceptionType {
    MISSING_IN_LEDGER,
    MISSING_IN_BANK_FILE,
    AMOUNT_MISMATCH,
    DUPLICATE,
    STATUS_MISMATCH
};

struct ReconciliationException {
    std::string          batch_id;
    ExceptionType         type;
    std::optional<std::string> ledger_transaction_id;
    std::string            bank_reference;
    int64_t                  ledger_amount_minor{0};
    int64_t                  bank_amount_minor{0};
};

} // namespace recon
```

## 5. The matching algorithm

```cpp
// recon/src/matcher.cpp — simplified core loop

std::vector<ReconciliationException> reconcile_batch(
    const std::vector<SettlementRecord>& ledger_records,
    const std::vector<SettlementRecord>& bank_records,
    const MatchConfig& cfg   // e.g. amount_tolerance_minor, allowed for FX rounding
) {
    // Index ledger records by external_reference for O(1) lookup — build once.
    absl::flat_hash_map<std::string, const SettlementRecord*> ledger_index;
    for (auto& r : ledger_records) ledger_index[r.external_reference] = &r;

    std::vector<ReconciliationException> exceptions;
    std::mutex exceptions_mutex;

    // Parallel scan over bank_records — this is the throughput-bound part.
    std::for_each(std::execution::par_unseq, bank_records.begin(), bank_records.end(),
        [&](const SettlementRecord& bank_row) {
            auto it = ledger_index.find(bank_row.external_reference);
            if (it == ledger_index.end()) {
                push_exception(exceptions, exceptions_mutex,
                    ExceptionType::MISSING_IN_LEDGER, bank_row);
                return;
            }
            const auto& ledger_row = *it->second;

            int64_t diff = std::abs(ledger_row.amount_minor - bank_row.amount_minor);
            if (diff > cfg.amount_tolerance_minor) {
                push_exception(exceptions, exceptions_mutex,
                    ExceptionType::AMOUNT_MISMATCH, bank_row, ledger_row);
                return;
            }
            if (ledger_row.status != bank_row.status) {
                push_exception(exceptions, exceptions_mutex,
                    ExceptionType::STATUS_MISMATCH, bank_row, ledger_row);
            }
        });

    // Second pass: anything in ledger_records not touched above is MISSING_IN_BANK_FILE.
    mark_untouched_ledger_rows_as_missing(ledger_records, bank_records, exceptions);

    return exceptions;
}
```

Real-world detail worth keeping: `amount_tolerance_minor` exists because FX conversions and fee rounding on the bank side legitimately produce paisa-level differences — a hardcoded `==` comparison is a rookie mistake that generates thousands of false-positive exceptions in production.

## 6. File ingestion (fixed-width settlement file parser)

```cpp
// recon/src/file_parser.cpp
// Real bank settlement files are frequently fixed-width. Example layout (illustrative):
//   cols 1-20   : reference number
//   cols 21-33  : amount (13 digits, implied 2 decimals, zero-padded)
//   cols 34-36  : currency
//   cols 37-43  : status code
struct FixedWidthLayout {
    size_t ref_start = 0,  ref_len = 20;
    size_t amt_start = 20, amt_len = 13;
    size_t ccy_start = 33, ccy_len = 3;
    size_t sts_start = 36, sts_len = 7;
};

SettlementRecord parse_line(std::string_view line, const FixedWidthLayout& layout);
```

Keep the layout in a config struct, not hardcoded offsets scattered through the code — bank file formats change per-bank and per-network, and a real reconciliation team maintains a layout registry.

## 7. Output contract back to the rest of the system

The C++ binary/service writes exceptions directly into `reconciliation_exceptions` (via `libpqxx`, batched `COPY` for throughput) **and** emits a summary that the Python orchestration layer (doc 03/07) picks up and republishes as `reconciliation.batch.completed` / `reconciliation.exception.raised` Kafka events per the schema in doc 05 — the C++ process itself does not need a Kafka client.

## 8. What Agent B ships

1. `SettlementRecord`/`ReconciliationException` types + fixed-width parser with unit tests on malformed rows
2. Single-threaded matcher correctness (small fixture files, hand-verified expected exceptions)
3. Parallelize the matcher, benchmark on a synthetic 1M-row batch
4. `libpqxx` write path into `reconciliation_exceptions` with batched `COPY`
5. CLI entrypoint: `recon_engine --ledger-export=... --bank-file=... --batch-id=...`
6. Integration test: run against a batch produced by Agent A's Rust engine's test fixtures
