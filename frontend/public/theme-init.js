// Applied before first paint (a classic script, so the CSP needs no inline exception).
(function () {
  try {
    var stored = localStorage.getItem("autoswe.theme");
    var dark = stored ? stored === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.classList.toggle("dark", dark);
  } catch (e) {
    /* storage unavailable: fall back to light */
  }
})();
