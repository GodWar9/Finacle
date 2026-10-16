var Portal = {
  start: async function (ready) {
    var banner = document.getElementById("portal-mode");
    if (USE_MOCK_DATA) { banner.textContent = "DEMO · Synthetic data · Changes reset on reload"; await Api.getAccounts(); ready(); return; }
    banner.textContent = "Live workspace · Services not yet verified";
    try {
      var session = await fetch("/portal/session").then(checkedJson);
      if (!session.authenticated) {
        document.getElementById("portal-login").hidden = false;
        document.querySelector(".side-nav").hidden = true;
        if (!session.configured) document.getElementById("login-error").textContent = "Live workspace needs operator configuration. Demo remains available.";
        document.getElementById("login-form").onsubmit = async function (event) {
          event.preventDefault();
          try {
            await fetch("/portal/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: document.getElementById("portal-password").value }) }).then(checkedJson);
            location.reload();
          } catch (error) { document.getElementById("login-error").textContent = error.message; }
        };
        return;
      }
      banner.textContent = "Live workspace · Latest loaded records";
      document.getElementById("portal-logout").hidden = false;
      document.getElementById("portal-logout").onclick = async function () {
        try { await fetch("/portal/logout", { method: "POST" }).then(checkedJson); location.reload(); }
        catch (error) { showLoadError(error); }
      };
      await Api.getAccounts();
      ready();
    } catch (error) { showLoadError(error); }
  }
};
