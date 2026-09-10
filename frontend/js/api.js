/*
  api.js
  ------
  Everything that would talk to the real backend lives here, in one
  place, so the rest of the frontend never calls fetch() directly.

  Right now USE_MOCK_DATA is true, so every function just returns the
  data from mock-data.js (wrapped in a fake Promise + small delay so
  the UI code that calls these functions doesn't need to change later
  when we flip this to false and point at the real gateway from
  03_API_GATEWAY_AND_IDEMPOTENCY_PYTHON.md).

  To wire this up to the real backend: set USE_MOCK_DATA = false and
  fill in GATEWAY_BASE_URL / RAG_BASE_URL below.
*/

var USE_MOCK_DATA = true;
var GATEWAY_BASE_URL = "http://localhost:8000";
var RAG_BASE_URL = "http://localhost:8001";
var API_KEY = "";  // set to your X-API-Key value when USE_MOCK_DATA = false

var Api = {

  // Small helper so every mock call "feels" like a real network call.
  _fakeDelay: function (value, ms) {
    return new Promise(function (resolve) {
      setTimeout(function () {
        resolve(value);
      }, ms || 250);
    });
  },

  getAccounts: function () {
    if (USE_MOCK_DATA) {
      return this._fakeDelay(MOCK_DATA.accounts);
    }
    return fetch(GATEWAY_BASE_URL + "/api/v1/accounts", {
      headers: { "X-API-Key": API_KEY }
    }).then(function (r) {
      return r.json();
    });
  },

  getTransactions: function () {
    if (USE_MOCK_DATA) {
      // Newest first, same as a real journal feed would return.
      var sorted = MOCK_DATA.transactions.slice().sort(function (a, b) {
        return new Date(b.created_at) - new Date(a.created_at);
      });
      return this._fakeDelay(sorted);
    }
    return fetch(GATEWAY_BASE_URL + "/api/v1/transactions", {
      headers: { "X-API-Key": API_KEY }
    }).then(function (r) {
      return r.json();
    });
  },

  // Mirrors POST /api/v1/transactions from doc 03. In mock mode we
  // just validate balance and append it locally so the UI feels real.
  postTransaction: function (idempotencyKey, entries, narrative, transactionType) {
    var self = this;

    var debitTotal = 0;
    var creditTotal = 0;
    entries.forEach(function (e) {
      if (e.direction === "DEBIT") debitTotal += e.amount_minor;
      if (e.direction === "CREDIT") creditTotal += e.amount_minor;
    });

    if (debitTotal !== creditTotal) {
      return Promise.reject(new Error(
        "Unbalanced transaction: debit " + debitTotal + " != credit " + creditTotal
      ));
    }

    if (USE_MOCK_DATA) {
      var newTxn = {
        transaction_id: "txn_" + Math.random().toString(36).slice(2, 8),
        transaction_type: transactionType || "PAYMENT",
        reference_id: idempotencyKey,
        status: "POSTED",
        narrative: narrative || "",
        created_at: new Date().toISOString(),
        entries: entries
      };
      MOCK_DATA.transactions.push(newTxn);
      return self._fakeDelay(newTxn, 400);
    }

    return fetch(GATEWAY_BASE_URL + "/api/v1/transactions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
        "X-API-Key": API_KEY
      },
      body: JSON.stringify({
        transaction_type: transactionType,
        entries: entries,
        narrative: narrative
      })
    }).then(function (r) {
      if (!r.ok) throw new Error("gateway rejected transaction: " + r.status);
      return r.json();
    });
  },

  getReconciliationExceptions: function () {
    if (USE_MOCK_DATA) {
      return this._fakeDelay(MOCK_DATA.reconciliationExceptions);
    }
    return fetch(GATEWAY_BASE_URL + "/api/v1/reconciliation/exceptions", {
      headers: { "X-API-Key": API_KEY }
    }).then(function (r) {
      return r.json();
    });
  },

  // Mirrors POST /api/v1/rag/ask from doc 04.
  askCopilot: function (question, scope) {
    if (USE_MOCK_DATA) {
      var lowerQ = question.toLowerCase();
      var found = MOCK_DATA.copilotAnswers.find(function (candidate) {
        if (candidate.scope !== scope) return false;
        return candidate.match.some(function (keyword) {
          return lowerQ.indexOf(keyword) !== -1;
        });
      });
      return this._fakeDelay(found || MOCK_DATA.copilotFallback, 600);
    }
    return fetch(RAG_BASE_URL + "/api/v1/rag/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({ question: question, scope: scope })
    }).then(function (r) {
      return r.json();
    });
  }
};
