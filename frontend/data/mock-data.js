/*
  mock-data.js
  ------------
  Stand-in data for the console so the frontend is fully demoable
  before the real backend (docs 01-05) exists or is running.

  IMPORTANT: the shapes here deliberately match the schema in
  05_DATABASE_AND_EVENT_SCHEMA.md — accounts, transactions,
  ledger_entries, reconciliation_exceptions — so swapping this file
  out for real fetch() calls in api.js later is a small change, not
  a rewrite.

  Everything hangs off a single global object, MOCK_DATA, since we
  are not using any module system (no build step, no framework).
*/

var MOCK_DATA = {

  accounts: [
    { account_id: "acc_merchant_wallet", account_number: "1000-0001", account_type: "LIABILITY", owner_ref: "merchant_9f2a" },
    { account_id: "acc_settlement",      account_number: "1000-0002", account_type: "ASSET",     owner_ref: "platform" },
    { account_id: "acc_fee_revenue",     account_number: "1000-0003", account_type: "REVENUE",   owner_ref: "platform" },
    { account_id: "acc_refund_expense",  account_number: "1000-0004", account_type: "EXPENSE",   owner_ref: "platform" }
  ],

  // Each transaction carries its own balanced set of entries, exactly
  // like a row in `transactions` joined with its `ledger_entries`.
  transactions: [
    {
      transaction_id: "txn_a1",
      transaction_type: "PAYMENT",
      reference_id: "order_9f2a11",
      status: "POSTED",
      narrative: "Payment capture for order 9f2a11",
      created_at: "2026-08-25T09:12:04Z",
      entries: [
        { account_id: "acc_settlement",     direction: "DEBIT",  amount_minor: 150000, currency: "INR" },
        { account_id: "acc_merchant_wallet", direction: "CREDIT", amount_minor: 150000, currency: "INR" }
      ]
    },
    {
      transaction_id: "txn_a2",
      transaction_type: "FEE",
      reference_id: "order_9f2a11",
      status: "POSTED",
      narrative: "Platform fee for order 9f2a11",
      created_at: "2026-08-25T09:12:05Z",
      entries: [
        { account_id: "acc_merchant_wallet", direction: "DEBIT",  amount_minor: 3500, currency: "INR" },
        { account_id: "acc_fee_revenue",     direction: "CREDIT", amount_minor: 3500, currency: "INR" }
      ]
    },
    {
      transaction_id: "txn_a3",
      transaction_type: "REFUND",
      reference_id: "order_7b1c88",
      status: "POSTED",
      narrative: "Partial refund for order 7b1c88",
      created_at: "2026-08-25T08:47:41Z",
      entries: [
        { account_id: "acc_refund_expense", direction: "DEBIT",  amount_minor: 42000, currency: "INR" },
        { account_id: "acc_settlement",     direction: "CREDIT", amount_minor: 42000, currency: "INR" }
      ]
    },
    {
      transaction_id: "txn_a4",
      transaction_type: "REVERSAL",
      reference_id: "order_3d90fa",
      status: "REVERSED",
      narrative: "Reversal of duplicate charge on order 3d90fa",
      created_at: "2026-08-24T22:03:12Z",
      entries: [
        { account_id: "acc_merchant_wallet", direction: "DEBIT",  amount_minor: 89000, currency: "INR" },
        { account_id: "acc_settlement",      direction: "CREDIT", amount_minor: 89000, currency: "INR" }
      ]
    }
  ],

  // Shape matches reconciliation_exceptions in the schema doc.
  reconciliationExceptions: [
    {
      exception_id: "exc_1",
      batch_id: "batch_2026_08_24",
      exception_type: "AMOUNT_MISMATCH",
      ledger_transaction_id: "txn_a1",
      bank_reference: "order_9f2a11",
      ledger_amount_minor: 150000,
      bank_amount_minor: 149950,
      detected_at: "2026-08-24T23:10:00Z"
    },
    {
      exception_id: "exc_2",
      batch_id: "batch_2026_08_24",
      exception_type: "MISSING_IN_LEDGER",
      ledger_transaction_id: null,
      bank_reference: "order_5c22e0",
      ledger_amount_minor: null,
      bank_amount_minor: 62000,
      detected_at: "2026-08-24T23:10:01Z"
    },
    {
      exception_id: "exc_3",
      batch_id: "batch_2026_08_23",
      exception_type: "STATUS_MISMATCH",
      ledger_transaction_id: "txn_a4",
      bank_reference: "order_3d90fa",
      ledger_amount_minor: 89000,
      bank_amount_minor: 89000,
      detected_at: "2026-08-23T23:09:40Z"
    }
  ],

  // Canned answers for the RAG copilot mock, keyed loosely by keyword.
  // The real implementation (04_RAG_COMPLIANCE_COPILOT.md) replaces
  // this with an actual /api/v1/rag/ask call.
  copilotAnswers: [
    {
      match: ["9f2a11", "why", "exception"],
      scope: "ops",
      answer: "Order 9f2a11 was flagged as an AMOUNT_MISMATCH during batch batch_2026_08_24: the ledger recorded ₹1,500.00 but the bank settlement file recorded ₹1,499.50, a ₹0.50 difference outside the configured tolerance.",
      sources: [
        { source_type: "RECONCILIATION_EXCEPTION", source_ref: "exc_1" },
        { source_type: "TXN_NARRATIVE", source_ref: "txn_a1" }
      ]
    },
    {
      match: ["reverse", "48", "policy", "settled"],
      scope: "compliance",
      answer: "Per internal policy, a settled transaction may be reversed within 180 days by posting a new reversing entry; the original entry is never edited or deleted, consistent with the ledger's append-only design.",
      sources: [
        { source_type: "POLICY_DOC", source_ref: "policy_reversal_window.md" }
      ]
    }
  ],

  copilotFallback: {
    answer: "I don't have a source that answers this directly. Try asking about a specific order reference or a specific policy topic.",
    sources: []
  }
};
