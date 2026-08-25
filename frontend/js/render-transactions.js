/*
  render-transactions.js
  -----------------------
  Renders the TRANSACTIONS tab. Two parts:
    1. A form for building a new transaction, one entry row at a time,
       with a live "balance ticker" that only allows posting once
       debits == credits (mirrors the invariant enforced twice over
       in the real backend — see 01_LEDGER_CORE_ENGINE_RUST.md §4 and
       05_DATABASE_AND_EVENT_SCHEMA.md §1.4).
    2. A table of transactions already posted.
*/

var RenderTransactions = {

  // Local, in-memory state for the entry rows currently being built.
  // Not persisted anywhere — this is just form state.
  draftRows: [],
  rowCounter: 0,

  mount: function () {
    var container = document.getElementById("view-transactions");
    container.innerHTML =
      '<h1 class="view__title">Transactions</h1>' +
      '<p class="view__subtitle">Post a new balanced entry, or review the journal</p>' +
      '<section class="ledger-page">' +
        '<div class="ledger-page__header"><h2>New Transaction</h2></div>' +
        '<div class="entry-form">' +
          '<div id="entry-rows"></div>' +
          '<button class="btn btn--ghost" id="add-row-btn" type="button">+ Add entry row</button>' +
          '<div class="balance-ticker">' +
            '<div class="balance-ticker__totals">' +
              '<div><small>Debit total</small><span id="debit-total">\u20B90.00</span></div>' +
              '<div><small>Credit total</small><span id="credit-total">\u20B90.00</span></div>' +
            '</div>' +
            '<span class="balance-ticker__verdict balance-ticker__verdict--unbalanced" id="balance-verdict">UNBALANCED</span>' +
          '</div>' +
          '<div style="margin-top: 16px; display:flex; gap:12px; align-items:center;">' +
            '<input type="text" id="txn-narrative" placeholder="Narrative (optional)" ' +
              'style="flex:1; font-family:var(--font-body); padding:8px; border:1px solid var(--color-line-paper); border-radius:3px;">' +
            '<button class="btn btn--primary" id="post-txn-btn" type="button" disabled>Post Transaction</button>' +
          '</div>' +
          '<p id="post-txn-message" style="font-size:0.8rem; margin-top:8px;"></p>' +
        '</div>' +
      '</section>' +
      '<section class="ledger-page">' +
        '<div class="ledger-page__header"><h2>Journal</h2></div>' +
        '<table class="ledger-table">' +
          '<thead><tr>' +
            '<th>Txn</th><th>Type</th><th>Narrative</th><th>Posted</th><th>Status</th>' +
          '</tr></thead>' +
          '<tbody id="txn-table-body"></tbody>' +
        '</table>' +
      '</section>';

    this.draftRows = [];
    this.rowCounter = 0;
    this.addRow("DEBIT");
    this.addRow("CREDIT");

    var self = this;
    document.getElementById("add-row-btn").addEventListener("click", function () {
      self.addRow("DEBIT");
    });
    document.getElementById("post-txn-btn").addEventListener("click", function () {
      self.postTransaction();
    });

    this.loadJournal();
  },

  addRow: function (direction) {
    var rowId = "row_" + (this.rowCounter++);
    this.draftRows.push({ id: rowId, direction: direction, accountId: MOCK_DATA.accounts[0].account_id, amountMinor: 0 });
    this.renderRows();
  },

  removeRow: function (rowId) {
    this.draftRows = this.draftRows.filter(function (r) { return r.id !== rowId; });
    this.renderRows();
  },

  renderRows: function () {
    var self = this;
    var container = document.getElementById("entry-rows");

    var accountOptions = MOCK_DATA.accounts.map(function (a) {
      return '<option value="' + a.account_id + '">' + a.account_number + ' — ' + a.account_type + '</option>';
    }).join("");

    container.innerHTML = this.draftRows.map(function (row) {
      return (
        '<div class="entry-row" data-row-id="' + row.id + '">' +
          '<select class="row-account">' + accountOptions + '</select>' +
          '<select class="row-direction">' +
            '<option value="DEBIT"' + (row.direction === "DEBIT" ? " selected" : "") + '>Debit</option>' +
            '<option value="CREDIT"' + (row.direction === "CREDIT" ? " selected" : "") + '>Credit</option>' +
          '</select>' +
          '<input class="row-amount" type="number" min="0" step="0.01" placeholder="0.00">' +
          '<button class="entry-row__remove" type="button">remove</button>' +
        '</div>'
      );
    }).join("");

    // Wire up listeners after re-render (simple approach — no
    // virtual DOM here, this is a plain script file, not a framework).
    this.draftRows.forEach(function (row) {
      var rowEl = container.querySelector('[data-row-id="' + row.id + '"]');
      rowEl.querySelector(".row-account").value = row.accountId;
      rowEl.querySelector(".row-account").addEventListener("change", function (e) {
        row.accountId = e.target.value;
        self.updateTicker();
      });
      rowEl.querySelector(".row-direction").addEventListener("change", function (e) {
        row.direction = e.target.value;
        self.updateTicker();
      });
      rowEl.querySelector(".row-amount").addEventListener("input", function (e) {
        var rupees = parseFloat(e.target.value) || 0;
        row.amountMinor = Math.round(rupees * 100);
        self.updateTicker();
      });
      rowEl.querySelector(".entry-row__remove").addEventListener("click", function () {
        self.removeRow(row.id);
      });
    });

    this.updateTicker();
  },

  // This is the live balance check. It recomputes on every keystroke
  // so the operator sees, in real time, whether what they've built so
  // far would be accepted by the backend's balance invariant.
  updateTicker: function () {
    var debitTotal = 0;
    var creditTotal = 0;
    this.draftRows.forEach(function (row) {
      if (row.direction === "DEBIT") debitTotal += row.amountMinor;
      else creditTotal += row.amountMinor;
    });

    document.getElementById("debit-total").textContent = Format.money(debitTotal, "INR");
    document.getElementById("credit-total").textContent = Format.money(creditTotal, "INR");

    var verdictEl = document.getElementById("balance-verdict");
    var postBtn = document.getElementById("post-txn-btn");
    var isBalanced = debitTotal === creditTotal && debitTotal > 0;

    if (isBalanced) {
      verdictEl.textContent = "BALANCED";
      verdictEl.className = "balance-ticker__verdict balance-ticker__verdict--balanced";
      postBtn.disabled = false;
    } else {
      verdictEl.textContent = "UNBALANCED";
      verdictEl.className = "balance-ticker__verdict balance-ticker__verdict--unbalanced";
      postBtn.disabled = true;
    }
  },

  postTransaction: function () {
    var self = this;
    var messageEl = document.getElementById("post-txn-message");
    var narrative = document.getElementById("txn-narrative").value;

    var entries = this.draftRows.map(function (row) {
      return {
        account_id: row.accountId,
        direction: row.direction,
        amount_minor: row.amountMinor,
        currency: "INR"
      };
    });

    // A fresh idempotency key per submit — in a real client this
    // would be generated once per logical user action and reused
    // across retries, exactly as described in
    // 03_API_GATEWAY_AND_IDEMPOTENCY_PYTHON.md §3.
    var idempotencyKey = "idem_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);

    messageEl.textContent = "Posting...";
    messageEl.style.color = "var(--color-paper-text-muted)";

    Api.postTransaction(idempotencyKey, entries, narrative, "PAYMENT").then(function () {
      messageEl.textContent = "Posted.";
      messageEl.style.color = "var(--color-ledger-green)";
      self.draftRows = [];
      self.rowCounter = 0;
      self.addRow("DEBIT");
      self.addRow("CREDIT");
      document.getElementById("txn-narrative").value = "";
      self.loadJournal();
    }).catch(function (err) {
      messageEl.textContent = err.message;
      messageEl.style.color = "var(--color-debit-red)";
    });
  },

  loadJournal: function () {
    Api.getTransactions().then(function (transactions) {
      var body = document.getElementById("txn-table-body");
      if (transactions.length === 0) {
        body.innerHTML = '<tr class="empty-row"><td colspan="5">No transactions yet. Post one above.</td></tr>';
        return;
      }
      body.innerHTML = transactions.map(function (txn) {
        return (
          '<tr>' +
            '<td>' + txn.transaction_id + '</td>' +
            '<td>' + txn.transaction_type + '</td>' +
            '<td>' + (txn.narrative || '\u2014') + '</td>' +
            '<td>' + Format.timestamp(txn.created_at) + '</td>' +
            '<td><span class="badge badge--' + Format.toCssSuffix(txn.status) + '">' + txn.status + '</span></td>' +
          '</tr>'
        );
      }).join("");
    });
  }
};
