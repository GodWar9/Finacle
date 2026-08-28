/*
  render-overview.js
  ------------------
  Renders the OVERVIEW tab: three headline stats plus a recent
  journal feed. This mirrors the "at a glance" role the overview
  section plays in 00_SYSTEM_OVERVIEW.md.
*/

var RenderOverview = {

  mount: function () {
    var container = document.getElementById("view-overview");
    container.innerHTML =
      '<h1 class="view__title">Overview</h1>' +
      '<p class="view__subtitle">Snapshot of ledger activity, refreshed on load</p>' +
      '<div class="stat-row" id="overview-stats"></div>' +
      '<section class="ledger-page">' +
        '<div class="ledger-page__header"><h2>Journal Feed</h2></div>' +
        '<table class="ledger-table">' +
          '<thead><tr>' +
            '<th>Txn</th><th>Type</th><th>Account</th><th>Dr / Cr</th><th>Amount</th><th>Status</th>' +
          '</tr></thead>' +
          '<tbody id="overview-journal-body"></tbody>' +
        '</table>' +
      '</section>';

    this.load();
  },

  load: function () {
    var self = this;
    Api.getTransactions().then(function (transactions) {
      self.renderStats(transactions);
      self.renderJournal(transactions);
    });
  },

  renderStats: function (transactions) {
    var totalLedgerMinor = 0;
    var today = new Date().toDateString();
    var txnsToday = 0;

    transactions.forEach(function (txn) {
      txn.entries.forEach(function (entry) {
        if (entry.direction === "CREDIT") totalLedgerMinor += entry.amount_minor;
      });
      if (new Date(txn.created_at).toDateString() === today) txnsToday++;
    });

    Api.getReconciliationExceptions().then(function (exceptions) {
      var openCount = exceptions.length;
      document.getElementById("overview-stats").innerHTML =
        statCard("Total Ledger Volume", Format.money(totalLedgerMinor, "INR")) +
        statCard("Transactions Today", String(txnsToday)) +
        statCard("Open Exceptions", String(openCount));
    });

    function statCard(label, value) {
      return (
        '<div class="stat-card">' +
          '<p class="stat-card__label">' + label + '</p>' +
          '<p class="stat-card__value">' + value + '</p>' +
        '</div>'
      );
    }
  },

  renderJournal: function (transactions) {
    var body = document.getElementById("overview-journal-body");

    if (transactions.length === 0) {
      body.innerHTML = '<tr class="empty-row"><td colspan="6">No transactions posted yet.</td></tr>';
      return;
    }

    var rows = "";
    // Show the most recent entries across the most recent few transactions.
    transactions.slice(0, 6).forEach(function (txn) {
      txn.entries.forEach(function (entry) {
        var amountClass = entry.direction === "DEBIT" ? "amount--debit" : "amount--credit";
        rows +=
          '<tr>' +
            '<td>' + txn.transaction_id + '</td>' +
            '<td>' + txn.transaction_type + '</td>' +
            '<td>' + Format.accountLabel(entry.account_id) + '</td>' +
            '<td>' + entry.direction + '</td>' +
            '<td class="' + amountClass + '">' + Format.money(entry.amount_minor, entry.currency) + '</td>' +
            '<td><span class="badge badge--' + Format.toCssSuffix(txn.status) + '">' + txn.status + '</span></td>' +
          '</tr>';
      });
    });
    body.innerHTML = rows;
  }
};
