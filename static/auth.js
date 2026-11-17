// Shared by the login/register pages and the interface.
(() => {
  // Interface: if the session expires, any API call returns 401 -> send the user to the login page.
  const realFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const res = await realFetch(...args);
    if (res.status === 401 && location.pathname.startsWith("/app")) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname);
    }
    return res;
  };

  // Login/register: show/hide password buttons.
  document.querySelectorAll("[data-toggle-pw]").forEach(btn => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.togglePw);
      input.type = input.type === "password" ? "text" : "password";
      btn.textContent = input.type === "password" ? "show" : "hide";
    });
  });

  // Register: live feedback on password length / match.
  const pw = document.getElementById("password"), cf = document.getElementById("confirm"), hint = document.getElementById("pw-hint");
  if (pw && cf && hint) {
    const check = () => {
      if (pw.value.length && pw.value.length < 6) { hint.textContent = "Too short - at least 6 characters."; hint.className = "hintm bad"; }
      else if (cf.value && pw.value !== cf.value) { hint.textContent = "The passwords do not match yet."; hint.className = "hintm bad"; }
      else if (cf.value && pw.value === cf.value) { hint.textContent = "Passwords match."; hint.className = "hintm good"; }
      else { hint.textContent = "At least 6 characters."; hint.className = "hintm"; }
    };
    pw.addEventListener("input", check); cf.addEventListener("input", check);
  }
})();