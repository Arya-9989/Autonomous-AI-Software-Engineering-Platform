// Runs inline in <head> before the first paint (the server's CSP admits it by its sha256 hash).
// 1. Appearance: the stored choice, else the OS setting; blocked storage also falls back to
//    the OS. theme-color follows, so the browser chrome matches a manual choice too.
// 2. The product page and the studio are lazily loaded chunks: start fetching the one this URL
//    needs in parallel with the entry instead of after it. __LANDING_CHUNKS__ and
//    __STUDIO_CHUNKS__ are replaced at build time with each chunk and its static imports.
// 3. On a job page, request the job in parallel with the scripts; src/lib/api.ts takes the
//    response instead of starting the same request once React has rendered.
(function () {
  var root = document.documentElement;
  var dark = null;
  var key = null;
  try {
    var stored = localStorage.getItem("autoswe.theme");
    if (stored === "dark" || stored === "light") dark = stored === "dark";
    key = localStorage.getItem("autoswe.apiKey");
  } catch (e) {
    /* storage unavailable: follow the OS */
  }
  if (dark === null) dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  root.classList.toggle("dark", dark);
  var metas = document.querySelectorAll('meta[name="theme-color"]');
  for (var i = 0; i < metas.length; i++) metas[i].setAttribute("content", dark ? "#000000" : "#ffffff");

  var landing = location.pathname === "/";
  var chunks = landing ? __LANDING_CHUNKS__ : __STUDIO_CHUNKS__;
  for (var j = 0; j < chunks.length; j++) {
    var link = document.createElement("link");
    link.rel = "modulepreload";
    link.crossOrigin = "";
    link.href = chunks[j];
    document.head.appendChild(link);
  }

  var job = !landing && /^\/app\/jobs\/([^/]+)\/?$/.exec(location.pathname);
  if (job && window.fetch) {
    var url = "/api/v1/jobs/" + job[1];
    var headers = { Accept: "application/json" };
    if (key) headers.Authorization = "Bearer " + key;
    var response = fetch(url, { headers: headers });
    response.catch(function () {
      /* the app retries; never an unhandled rejection */
    });
    window.__autosweEarly = { url: url, response: response };
  }
})();
