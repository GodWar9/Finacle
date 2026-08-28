/*
  main.js
  -------
  The entry point. Wires up the left-nav tabs to the four views and
  mounts the initially-active one. Each view's own render-*.js file
  owns everything inside its <div id="view-...">; this file only
  decides which one is visible.
*/

document.addEventListener("DOMContentLoaded", function () {

  var views = {
    overview: RenderOverview,
    transactions: RenderTransactions,
    reconciliation: RenderReconciliation,
    copilot: RenderCopilot
  };

  var mounted = {}; // track which views have been mounted already

  function showView(name) {
    // Toggle visibility of the view panels.
    Object.keys(views).forEach(function (key) {
      var panel = document.getElementById("view-" + key);
      panel.classList.toggle("view--active", key === name);
    });

    // Toggle the active state on the nav buttons.
    document.querySelectorAll(".side-nav__item").forEach(function (btn) {
      btn.classList.toggle("side-nav__item--active", btn.getAttribute("data-view") === name);
    });

    // Mount the view's content the first time it's shown. We don't
    // re-fetch every time you click back to a tab you've already
    // visited — each render-*.js file can add its own refresh button
    // later if that's needed.
    if (!mounted[name]) {
      views[name].mount();
      mounted[name] = true;
    }
  }

  document.querySelectorAll(".side-nav__item").forEach(function (btn) {
    btn.addEventListener("click", function () {
      showView(btn.getAttribute("data-view"));
    });
  });

  // Start on the overview tab.
  showView("overview");
});
