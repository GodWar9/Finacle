/*
  render-copilot.js
  ------------------
  Renders the COPILOT tab: a chat interface over the RAG service from
  04_RAG_COMPLIANCE_COPILOT.md. Two scopes, matching the two corpora
  that service keeps separate — "ops" (transaction narratives and
  exceptions) and "compliance" (policy docs and regulatory circulars).
  Every answer shows its sources; if there's nothing grounded to say,
  the fallback answer says so instead of guessing (see doc 04 §4).
*/

var RenderCopilot = {

  scope: "ops",
  history: [],

  mount: function () {
    var container = document.getElementById("view-copilot");
    container.innerHTML =
      '<h1 class="view__title">Ask the Copilot</h1>' +
      '<p class="view__subtitle">Grounded Q&A over operational data and compliance policy — answers cite their sources</p>' +
      '<section class="ledger-page">' +
        '<div class="copilot-scope">' +
          '<button class="chip chip--active" data-scope="ops">Ops</button>' +
          '<button class="chip" data-scope="compliance">Compliance</button>' +
        '</div>' +
        '<div class="copilot-log" id="copilot-log">' +
          '<div class="empty-state">Ask about a specific order reference (ops) or a policy topic (compliance).</div>' +
        '</div>' +
        '<div class="copilot-input-row">' +
          '<input type="text" id="copilot-input" placeholder="e.g. Why did order 9f2a11 end up in exceptions?">' +
          '<button class="btn btn--primary" id="copilot-ask-btn" type="button">Ask</button>' +
        '</div>' +
      '</section>';

    var self = this;
    container.querySelectorAll(".copilot-scope .chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        self.scope = chip.getAttribute("data-scope");
        container.querySelectorAll(".copilot-scope .chip").forEach(function (c) {
          c.classList.remove("chip--active");
        });
        chip.classList.add("chip--active");
      });
    });

    document.getElementById("copilot-ask-btn").addEventListener("click", function () {
      self.ask();
    });
    document.getElementById("copilot-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") self.ask();
    });
  },

  ask: function () {
    var input = document.getElementById("copilot-input");
    var question = input.value.trim();
    if (!question) return;

    this.history.push({ role: "user", text: question });
    this.renderLog();
    input.value = "";

    var self = this;
    Api.askCopilot(question, this.scope).then(function (result) {
      self.history.push({ role: "assistant", text: result.answer, sources: result.sources });
      self.renderLog();
    });
  },

  renderLog: function () {
    var logEl = document.getElementById("copilot-log");

    logEl.innerHTML = this.history.map(function (msg) {
      if (msg.role === "user") {
        return '<div class="copilot-message copilot-message--user">' + escapeHtml(msg.text) + '</div>';
      }
      var sourcesHtml = "";
      if (msg.sources && msg.sources.length > 0) {
        sourcesHtml = '<div class="copilot-sources">' + msg.sources.map(function (s) {
          return '<div class="copilot-source">' + s.source_type + ' \u2014 ' + s.source_ref + '</div>';
        }).join("") + '</div>';
      }
      return '<div class="copilot-message copilot-message--assistant">' + escapeHtml(msg.text) + sourcesHtml + '</div>';
    }).join("");

    logEl.scrollTop = logEl.scrollHeight;

    function escapeHtml(str) {
      var div = document.createElement("div");
      div.textContent = str;
      return div.innerHTML;
    }
  }
};
