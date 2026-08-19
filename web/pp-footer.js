(function(){
  // Path & Pixel shared footer: full (homepage), short (inner pages), and
  // legal (focused flows — sign in/up, reset, onboarding). Same pattern as
  // pp-nav.js: self-contained, one <script> tag, reads tokens.css's
  // --pp-*/--c-* custom properties.
  //
  // Not yet wired into any live Kazi page — see pp-nav.js's header comment.
  //
  // Usage:
  //   <div id="pp-footer" data-variant="full"></div>    homepage
  //   <div id="pp-footer" data-variant="short"></div>   every other inner page
  //   <div id="pp-footer" data-variant="legal"></div>   focused flows (pairs with
  //                                                       #pp-nav[data-shell=focused])
  if (window.PathPixelFooter) return;

  function ready(fn){
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  var ROUTES = (window.PathPixelNav && window.PathPixelNav.ROUTES) || {
    home: "/", jobs: "/jobs", people: "/people", community: "/community", resources: "/resources"
  };

  // Each platform ships two files: the "-2" glyph is white, for the dark
  // homepage footer; the plain glyph is dark, for the cream inner-page band.
  // Picking the wrong one makes the whole row invisible.
  var SOCIALS_FULL = [
    { label: "facebook", file: "/social-icons/facebook-2.svg" },
    { label: "Instagram", file: "/social-icons/instagram-2.svg" },
    { label: "LinkedIn", file: "/social-icons/linkedin-2.svg" },
    { label: "TikTok", file: "/social-icons/tiktok-2.svg" },
    { label: "X", file: "/social-icons/x-2.svg" },
    { label: "YouTube", file: "/social-icons/youtube-2.svg" }
  ];
  var SOCIALS_SHORT = [
    { label: "facebook", file: "/social-icons/facebook.svg" },
    { label: "Instagram", file: "/social-icons/instagram.svg" },
    { label: "LinkedIn", file: "/social-icons/linkedin.svg" },
    { label: "TikTok", file: "/social-icons/tiktok.svg" },
    { label: "X", file: "/social-icons/x.svg" }
  ];

  var STYLE = ""
    // --c-surface-dark, not --pp-ink: same value (#0A0A0A) but names the
    // intent — "a band that was already ink stays the page ground" is one
    // of the design system's documented dark-mode exceptions, so blending
    // into the dark-theme page background here is correct, not a bug.
    + ".pp-foot-full{background:var(--c-surface-dark);color:var(--pp-text-faint-on-dark,#D2D2D6)}"
    + ".pp-foot-full-cols{max-width:var(--pp-container);margin:0 auto;padding:56px var(--pp-page-margin) 40px;"
    + "display:grid;grid-template-columns:minmax(0,1.4fr) repeat(3,minmax(0,1fr));gap:48px}"
    + "@media(max-width:767px){.pp-foot-full-cols{grid-template-columns:1fr 1fr;gap:22px 16px;padding:32px 16px 24px}}"
    + ".pp-foot-brand img{height:48px;display:block;margin-bottom:12px}"
    + "@media(max-width:767px){.pp-foot-brand img{height:44px;margin-bottom:12px}}"
    + ".pp-foot-brand p{margin:0;font-size:14px;line-height:1.6;color:var(--pp-text-subtle-on-dark,#A1A1A8);max-width:34ch}"
    + ".pp-foot-col{display:flex;flex-direction:column;gap:10px}"
    + ".pp-foot-col>span{font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--pp-text-subtle-on-dark,#A1A1A8);margin-bottom:2px}"
    + ".pp-foot-col a{font-size:14px;color:var(--pp-text-faint-on-dark,#D2D2D6);text-decoration:none}"
    + ".pp-foot-col a:hover{color:var(--pp-gold)}"
    + ".pp-foot-full-bottom{max-width:var(--pp-container);margin:0 auto;padding:20px var(--pp-page-margin) 40px;"
    + "border-top:1px solid var(--pp-border-dark,#1F1F1F);display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}"
    + "@media(max-width:767px){.pp-foot-full-bottom{padding:16px 16px 16px;flex-direction:column;align-items:flex-start}}"
    + ".pp-foot-full-bottom>span{font-size:13px;color:var(--pp-text-subtle-on-dark,#A1A1A8)}"
    + ".pp-foot-social{display:flex;align-items:center;gap:36px}"
    + "@media(max-width:767px){.pp-foot-social{gap:20px}}"
    + ".pp-foot-social img{height:24px;display:block}"
    + ".pp-foot-social a{display:inline-flex}"
    + ".pp-foot-social a:hover{opacity:.65}"
    // ---- short (inner page) ----
    + ".pp-foot-short{background:var(--c-surface);border-top:1px solid var(--c-border);margin-top:auto}"
    + ".pp-foot-short-row{max-width:var(--pp-container);margin:0 auto;padding:20px var(--pp-page-margin) 40px;"
    + "display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}"
    + ".pp-foot-short-row>span{font-size:13px;color:var(--c-text-subtle)}"
    + ".pp-foot-short-social{display:flex;align-items:center;gap:20px}"
    + ".pp-foot-short-social a{display:inline-flex}"
    + ".pp-foot-short-social a:hover{opacity:.65}"
    + ".pp-foot-short-social img{height:20px;display:block}"
    // ---- legal (focused flows) ----
    + ".pp-foot-legal{padding:24px var(--pp-page-margin) 32px;display:flex;align-items:center;gap:20px;flex-wrap:wrap}"
    + ".pp-foot-legal span,.pp-foot-legal a{font-size:13px;color:var(--c-text-faint)}"
    + ".pp-foot-legal a{text-decoration:none}"
    + ".pp-foot-legal a:hover{color:var(--c-text)}";

  function injectStyle(){
    if (document.getElementById("pp-footer-style")) return;
    var s = document.createElement("style");
    s.id = "pp-footer-style";
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function fullHtml(){
    var year = new Date().getFullYear();
    var social = SOCIALS_FULL.map(function(s){
      return '<a href="#" aria-label="Path &amp; Pixel on ' + s.label + '"><img src="' + s.file + '" alt=""></a>';
    }).join("");
    return '<footer class="pp-foot-full">' +
      '<div class="pp-foot-full-cols">' +
        '<div class="pp-foot-brand"><img src="/brand/pp-logo.png" alt="Path &amp; Pixel">' +
          '<p>Built for designers across Africa.</p></div>' +
        '<div class="pp-foot-col"><span>Find</span>' +
          '<a href="' + ROUTES.jobs + '">Jobs</a>' +
          '<a href="' + ROUTES.people + '">Designers</a>' +
          '<a href="/companies">Companies</a>' +
          '<a href="' + ROUTES.community + '">Mentors</a></div>' +
        '<div class="pp-foot-col"><span>Build</span>' +
          '<a href="/dashboard">Your profile</a>' +
          '<a href="/dashboard">Portfolio</a>' +
          '<a href="' + ROUTES.resources + '">Résumé check</a>' +
          '<a href="/post-a-role">Post a role</a></div>' +
        '<div class="pp-foot-col"><span>About</span>' +
          '<a href="#">Why we exist</a>' +
          '<a href="#">How roles are checked</a>' +
          '<a href="/privacy">Privacy</a>' +
          '<a href="/terms">Terms</a></div>' +
      '</div>' +
      '<div class="pp-foot-full-bottom">' +
        '<span>© ' + year + ' Path &amp; Pixel</span>' +
        '<div class="pp-foot-social">' + social + '</div>' +
      '</div>' +
    '</footer>';
  }

  function shortHtml(){
    var year = new Date().getFullYear();
    var social = SOCIALS_SHORT.map(function(s){
      return '<a href="#" aria-label="Path &amp; Pixel on ' + s.label + '"><img src="' + s.file + '" alt=""></a>';
    }).join("");
    return '<footer class="pp-foot-short"><div class="pp-foot-short-row">' +
      '<span>© ' + year + ' Path &amp; Pixel</span>' +
      '<div class="pp-foot-short-social">' + social + '</div>' +
    '</div></footer>';
  }

  function legalHtml(){
    var year = new Date().getFullYear();
    return '<div class="pp-foot-legal">' +
      '<span>© ' + year + ' Path &amp; Pixel</span>' +
      '<a href="/privacy">Privacy</a>' +
      '<a href="/terms">Terms</a>' +
      '<a href="/resources">Help</a>' +
    '</div>';
  }

  function render(el, variant){
    injectStyle();
    variant = variant || el.getAttribute("data-variant") || "short";
    if (variant === "full") el.innerHTML = fullHtml();
    else if (variant === "legal") el.innerHTML = legalHtml();
    else el.innerHTML = shortHtml();
  }

  function init(){
    var root = document.getElementById("pp-footer");
    if (root) render(root);
  }

  window.PathPixelFooter = { render: render };
  ready(init);
})();
