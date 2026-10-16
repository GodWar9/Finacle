var USE_MOCK_DATA = new URLSearchParams(location.search).get("demo") === "1";
var GATEWAY_BASE_URL = "";
var RAG_BASE_URL = "";
function checkedJson(response) {
  if (!response.ok) throw new Error(response.status === 401 ? "Session expired. Reload and sign in." : "Service unavailable (" + response.status + "). Please retry.");
  return response.json();
}
function showLoadError(error) {
  var banner = document.getElementById("portal-error");
  banner.textContent = error.message;
  banner.hidden = false;
}

var Api = {

  accounts: [],
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
      this.accounts = MOCK_DATA.accounts;
      return this._fakeDelay(this.accounts);
    }
    var self = this;
    return fetch(GATEWAY_BASE_URL + "/api/v1/accounts?limit=100", {
      credentials: "same-origin"
    }).then(function (r) {
      return checkedJson(r);
    }).then(function (data) { self.accounts = data.accounts; return self.accounts; });
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
      credentials: "same-origin"
    }).then(function (r) {
      return checkedJson(r);
    }).then(function (data) { return data.transactions; });
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
        "Idempotency-Key": idempotencyKey
      },
      body: JSON.stringify({
        transaction_type: transactionType,
        entries: entries,
        narrative: narrative
      })
    }).then(function (r) {
      if (!r.ok) throw new Error("gateway rejected transaction: " + r.status);
      return checkedJson(r);
    });
  },

  getReconciliationExceptions: function () {
    if (USE_MOCK_DATA) {
      return this._fakeDelay(MOCK_DATA.reconciliationExceptions);
    }
    return fetch(GATEWAY_BASE_URL + "/api/v1/reconciliation/exceptions", {
      credentials: "same-origin"
    }).then(function (r) {
      return checkedJson(r);
    }).then(function (data) { return data.exceptions; });
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
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question, scope: scope })
    }).then(function (r) {
      return checkedJson(r);
    });
  }
};
