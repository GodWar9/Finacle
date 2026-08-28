/*
  render-reconciliation.js
  -------------------------
  Renders the RECONCILIATION tab: a filterable table of rows from
  reconciliation_exceptions (see 02_RECONCILIATION_ENGINE_CPP.md and
  05_DATABASE_AND_EVENT_SCHEMA.md §1.5). Every exception here is a
  case the three-way match could not cleanly resolve — nothing on
  this screen was auto-corrected.
*/

var RenderReconciliation = {

  allExceptions: [],
  activeFilter: "ALL",

  mount: function () {
    var container = document.getElementById("view-reconciliation");
    container.innerHTML =
      '<h1 class="view__title">Reconciliation Exceptions</h1>' +
      '<p class="view__subtitle">Rows the three-way match (ledger / gateway / bank file) could not cleanly resolve</p>' +
      '<section class="ledger-page">' +
        '<div class="filter-chips" id="recon-filters"></div>' +
        '<table class="ledger-table">' +
          '<thead><tr>' +
            '<th>Type</th><th>Batch</th><th>Ledger Txn</th><th>Bank Ref</th><th>Ledger Amt</th><th>Bank Amt</th><th>Detected</th>' +
          '</tr></thead>' +
          '<tbody id="recon-table-body"></tbody>' +
        '</table>' +
      '</section>';

    this.load();
  },

  load: function () {
    var self = this;
    Api.getReconciliationExceptions().then(function (exceptions) {
      self.allExceptions = exceptions;
      self.renderFilters();
      self.renderTable();
    });
  },

  renderFilters: function () {
    var self = this;
    var types = ["ALL"];
    this.allExceptions.forEach(function (e) {
      if (types.indexOf(e.exception_type) === -1) types.push(e.exception_type);
    });

    var chipsEl = document.getElementById("recon-filters");
    chipsEl.innerHTML = types.map(function (type) {
      var isActive = type === self.activeFilter;
      var label = type === "ALL" ? "All" : type.replace(/_/g, " ").toLowerCase();
      return (
        '<button class="chip' + (isActive ? " chip--active" : "") + '" data-type="' + type + '">' +
          label +
        '</button>'
      );
    }).join("");

    chipsEl.querySelectorAll(".chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        self.activeFilter = chip.getAttribute("data-type");
        self.renderFilters();
        self.renderTable();
      });
    });
  },

  renderTable: function () {
    var body = document.getElementById("recon-table-body");
    var rows = this.activeFilter === "ALL"
      ? this.allExceptions
      : this.allExceptions.filter(function (e) { return e.exception_type === this.activeFilter; }.bind(this));

    if (rows.length === 0) {
      body.innerHTML = '<tr class="empty-row"><td colspan="7">No exceptions of this type. Nothing to review.</td></tr>';
      return;
    }

    body.innerHTML = rows.map(function (e) {
      return (
        '<tr>' +
          '<td><span class="badge badge--' + Format.toCssSuffix(e.exception_type) + '">' + e.exception_type.replace(/_/g, " ") + '</span></td>' +
          '<td>' + e.batch_id + '</td>' +
          '<td>' + (e.ledger_transaction_id || '\u2014') + '</td>' +
          '<td>' + e.bank_reference + '</td>' +
          '<td>' + (e.ledger_amount_minor != null ? Format.money(e.ledger_amount_minor, "INR") : '\u2014') + '</td>' +
          '<td>' + (e.bank_amount_minor != null ? Format.money(e.bank_amount_minor, "INR") : '\u2014') + '</td>' +
          '<td>' + Format.timestamp(e.detected_at) + '</td>' +
        '</tr>'
      );
    }).join("");
  }
};
