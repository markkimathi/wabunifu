(function(){
  // Path & Pixel shared navigation chrome: desktop header with search,
  // mobile/tablet-portrait header with full-screen search + menu, and
  // breadcrumbs. Self-contained on purpose, like confirm-dialog.js and
  // featured-projects.js: one <script> tag, no per-page markup to keep in
  // sync. Reads tokens.css's --pp-*/--c-* custom properties, so it needs
  // <link rel="stylesheet" href="tokens.css"> on the page too.
  //
  // Not yet wired into any live Kazi page (see README's "Working on this
  // with Claude Code" section) — this is Phase 2 of the Path & Pixel
  // migration, built ahead of the screens that will use it.
  //
  // Usage:
  //   <div id="pp-nav" data-active="jobs"></div>
  //   <nav id="pp-breadcrumbs" data-crumbs='[{"label":"Jobs"}]'></nav>
  //   ...
  //   <script src="tokens.css">  (as a <link>, not a script)
  //   <script src="pp-nav.js?v=1"></script>
  //
  // #pp-nav attributes:
  //   data-active   "home"|"jobs"|"people"|"community"|"resources", omit for none active
  //   data-shell    "focused" renders just the mark (sign in/up/reset/onboarding), default "full"
  //   data-user     JSON {"name":"Amara Okonkwo","initials":"AO","href":"/people/amara"} — signed in;
  //                 omit for signed-out (Sign in / Join for free)
  //
  // #pp-breadcrumbs attributes:
  //   data-crumbs   JSON array of {label, href}. The last item has no href (current page).
  //                 "Home" is prepended automatically. Omit the element entirely on the homepage.
  if (window.PathPixelNav) return; // don't double-init if this ever loads twice

  // This script tag sits in <head>, so it runs before <body> exists —
  // defer to DOMContentLoaded, same pattern as nav.js's ready() helper.
  function ready(fn){
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  var ROUTES = {
    home: "/", jobs: "/jobs", people: "/people",
    community: "/community", resources: "/resources",
    postRole: "/post-a-role", signin: "/signin", join: "/join"
  };

  var DESIGNER_TOKEN_KEY = "kazi_designer_token";

  var NAV_ITEMS = [
    { key: "home", label: "Home", href: ROUTES.home,
      icon: '<path d="M4 11l8-7 8 7"/><path d="M6 9.5V20a1 1 0 001 1h4v-6h2v6h4a1 1 0 001-1V9.5"/>' },
    { key: "jobs", label: "Jobs", href: ROUTES.jobs,
      icon: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2"/>' },
    { key: "people", label: "People", href: ROUTES.people,
      icon: '<circle cx="9" cy="8" r="3.4"/><path d="M2.5 20a6.5 6.5 0 0113 0"/><path d="M16 5.5a3.2 3.2 0 010 6M17 14.6a6.2 6.2 0 014.5 5.4"/>' },
    { key: "community", label: "Community", href: ROUTES.community,
      icon: '<path d="M21 15a2 2 0 01-2 2H8l-4 4V5a2 2 0 012-2h13a2 2 0 012 2z"/>' },
    { key: "resources", label: "Resources", href: ROUTES.resources,
      icon: '<path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>' }
  ];

  function svg(size, inner, strokeWidth){
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="' + (strokeWidth || 2) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>';
  }
  // NAV_ITEMS[n].icon is bare path/shape markup (no <svg> wrapper), reused
  // both inline in the nav (sized via CSS) and here at an explicit size.
  function navIcon(item, size){ return svg(size, item.icon); }
  var ICONS = {
    search: function(s){ return svg(s, '<circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.5-3.5"></path>'); },
    close: function(s){ return svg(s, '<path d="M6 6l12 12M18 6L6 18"></path>'); },
    hamburger: function(s){ return svg(s, '<path d="M4 7h16M4 12h16M4 17h16"></path>'); },
    chevronRight: function(s){ return svg(s, '<path d="M9 6l6 6-6 6"></path>'); },
    back: function(s){ return svg(s, '<path d="M15 18l-6-6 6-6"></path>'); },
    clear: function(s){ return svg(s, '<path d="M6 6l12 12M18 6L6 18"></path>'); },
    clock: function(s){ return svg(s, '<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path>'); },
    theme: function(s){ return svg(s, '<circle cx="12" cy="12" r="4.5"></circle><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4"></path>'); }
  };

  var STYLE = ""
    // These elements have their own `display` rule AND are toggled via the
    // `hidden` attribute (clear buttons, the recent-searches row); an author
    // `display` declaration otherwise beats the UA [hidden] rule.
    + ".pp-search-clear[hidden],.pp-recent-row[hidden]{display:none!important}"
    // #pp-nav is a bare mount div with only the header inside it, so its
    // own box is auto-height == the header's height. A sticky element
    // can't stick past the bottom edge of its containing block, so
    // without this the header would stop sticking the instant the page
    // scrolls past ~57px (its own height) and scroll away with the rest
    // of the page. display:contents removes #pp-nav from the box tree so
    // the header's containing block is <body> (the full page) instead.
    + "#pp-nav{display:contents}"
    // ---- desktop header (>=1024px) ----
    + ".pp-header{position:sticky;top:0;z-index:var(--pp-z-header,6);background:var(--c-bg);border-bottom:1px solid var(--c-border)}"
    + ".pp-header-row{max-width:var(--pp-container);margin:0 auto;padding:0 var(--pp-page-margin);height:var(--pp-header-height);display:none;align-items:center;gap:20px}"
    + "@media(min-width:1024px){.pp-header-row{display:flex}}"
    + ".pp-mark{display:inline-flex;align-items:center;flex:none}"
    + ".pp-mark img{width:30px;height:30px;display:block}"
    + ".pp-dlinks{display:flex;align-items:center;gap:2px;flex:none}"
    + ".pp-dlinks a{font-size:var(--pp-nav-size);font-weight:var(--pp-nav-weight);padding:8px 12px;border-radius:8px;color:var(--c-text-muted);white-space:nowrap;text-decoration:none}"
    + ".pp-dlinks a:hover{background:var(--c-bg-sunken);color:var(--c-text)}"
    + ".pp-dlinks a.-active{color:var(--c-accent-text);font-weight:var(--pp-nav-weight-active)}"
    + ".pp-spacer{flex:1;min-width:12px}"
    + ".pp-icon-btn{width:40px;height:40px;flex:none;border:none;border-radius:9px;background:transparent;color:var(--c-text-muted);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;padding:0}"
    + ".pp-icon-btn:hover{background:var(--c-bg-sunken)}"
    + ".pp-theme-btn{width:40px;height:40px;flex:none;border:1px solid var(--c-border);border-radius:50%;background:transparent;color:var(--c-text);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;padding:0}"
    + ".pp-theme-btn:hover{background:var(--c-bg-sunken)}"
    + ".pp-post-link{font-size:var(--pp-nav-size);font-weight:var(--pp-nav-weight);padding:8px 12px;border-radius:8px;color:var(--c-text-muted);flex:none;white-space:nowrap;text-decoration:none}"
    + ".pp-post-link:hover{background:var(--c-bg-sunken);color:var(--c-text)}"
    + ".pp-auth{display:flex;align-items:center;gap:8px;flex:none}"
    + ".pp-btn-ghost{font-size:var(--pp-button-size);font-weight:var(--pp-button-weight);padding:0 14px;height:40px;display:inline-flex;align-items:center;border-radius:9px;border:1px solid var(--c-border-strong);color:var(--c-text);white-space:nowrap;text-decoration:none}"
    + ".pp-btn-ghost:hover{background:var(--c-bg-sunken);border-color:var(--c-border-hover)}"
    + ".pp-btn-gold{font-size:var(--pp-button-size);font-weight:var(--pp-button-weight);padding:0 18px;height:40px;display:inline-flex;align-items:center;border-radius:9px;background:var(--c-accent);color:var(--c-on-accent);white-space:nowrap;text-decoration:none}"
    + ".pp-btn-gold:hover{background:var(--c-accent-hover)}"
    + ".pp-avatar-menu{position:relative;flex:none}"
    + ".pp-avatar-chip{display:inline-flex;align-items:center;gap:9px;flex:none;padding:4px 4px 4px 12px;border-radius:999px;border:1px solid var(--c-border);background:transparent;text-decoration:none;color:var(--c-text);font:inherit;cursor:pointer}"
    + ".pp-avatar-chip:hover{background:var(--c-bg-sunken)}"
    + ".pp-avatar-chip .pp-av-name{font-size:14px;font-weight:500}"
    + ".pp-av-circle{width:32px;height:32px;border-radius:50%;background:var(--pp-gold-300);display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--pp-gold-800)}"
    + ".pp-avatar-dropdown{position:absolute;top:calc(100% + 8px);right:0;min-width:180px;z-index:var(--pp-z-menu,21);"
    + "background:var(--c-surface);border:1px solid var(--c-border-strong);border-radius:var(--pp-radius-lg);"
    + "box-shadow:var(--pp-shadow-lg);padding:6px;display:none;flex-direction:column;gap:2px}"
    + ".pp-avatar-dropdown.-open{display:flex}"
    + ".pp-avatar-dropdown a,.pp-avatar-dropdown button{display:block;width:100%;text-align:left;"
    + "padding:9px 10px;border-radius:8px;border:0;background:none;color:var(--c-text);"
    + "font:inherit;font-size:13.5px;font-weight:600;cursor:pointer;text-decoration:none}"
    + ".pp-avatar-dropdown a:hover,.pp-avatar-dropdown button:hover{background:var(--c-bg-sunken)}"
    // ---- desktop search drop ----
    + ".pp-search-drop{border-top:1px solid var(--c-border);background:var(--c-bg);display:none}"
    + ".pp-search-drop.-open{display:block}"
    + ".pp-search-inner{max-width:var(--pp-container);margin:0 auto;padding:18px var(--pp-page-margin) 22px}"
    + ".pp-search-field{display:flex;align-items:center;gap:10px;padding:0 16px;height:52px;border-radius:12px;background:var(--c-surface);border:1px solid var(--c-border-strong);box-shadow:var(--pp-shadow-md)}"
    + ".pp-search-field.-focus{border-color:var(--pp-gold);box-shadow:var(--pp-focus-ring)}"
    + ".pp-search-field svg{flex:none;color:var(--c-text-faint)}"
    + ".pp-search-field input{border:none;outline:none;font-family:var(--pp-font);font-size:16px;flex:1;min-width:0;background:transparent;color:var(--c-text)}"
    + ".pp-search-clear{width:28px;height:28px;flex:none;border:none;border-radius:50%;background:var(--c-surface-inset);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;color:var(--c-text-muted);padding:0}"
    + ".pp-search-esc{height:36px;padding:0 12px;border:none;border-radius:8px;background:var(--c-surface-inset);font-family:var(--pp-font);font-size:14px;font-weight:600;color:var(--c-text-muted);cursor:pointer;flex:none}"
    + ".pp-search-esc:hover{background:var(--c-border)}"
    + ".pp-recent-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center}"
    + ".pp-recent-row>span{font-size:13px;color:var(--c-text-subtle);margin-right:2px}"
    + ".pp-recent-chip{padding:7px 13px;border-radius:999px;border:1px solid var(--c-border);background:var(--c-surface);font-family:var(--pp-font);font-size:13.5px;font-weight:500;color:var(--c-text-muted);cursor:pointer}"
    + ".pp-recent-chip:hover{border-color:var(--c-border-hover);color:var(--c-text)}"
    // ---- compact header (<1024px: mobile + tablet portrait) ----
    + ".pp-header-compact{display:flex;height:56px;padding:0 16px;align-items:center;gap:8px}"
    + "@media(min-width:1024px){.pp-header-compact{display:none}}"
    + ".pp-header-compact img{width:26px;height:26px;display:block}"
    + ".pp-tap-btn{width:44px;height:44px;border:none;border-radius:10px;background:transparent;color:var(--c-text-muted);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;padding:0}"
    + ".pp-tap-btn:hover{background:var(--c-bg-sunken)}"
    + ".pp-tap-theme{width:44px;height:44px;flex:none;border:1px solid var(--c-border);border-radius:50%;background:transparent;color:var(--c-text);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;padding:0}"
    + ".pp-menu-btn{margin-right:-8px}"
    // ---- full-screen mobile search ----
    + ".pp-msearch{position:fixed;inset:0;z-index:var(--pp-z-menu,21);background:var(--c-bg);display:none;flex-direction:column}"
    + ".pp-msearch.-open{display:flex}"
    + ".pp-msearch-head{height:56px;padding:0 12px 0 16px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--c-border);flex:none}"
    + ".pp-msearch-field{display:flex;align-items:center;gap:9px;padding:0 12px;height:40px;flex:1;min-width:0;border-radius:10px;background:var(--c-surface);border:1px solid var(--c-border-strong)}"
    + ".pp-msearch-field.-focus{border-color:var(--pp-gold);box-shadow:var(--pp-focus-ring)}"
    + ".pp-msearch-field svg{flex:none;color:var(--c-text-faint)}"
    + ".pp-msearch-field input{border:none;outline:none;font-family:var(--pp-font);font-size:16px;flex:1;min-width:0;background:transparent;color:var(--c-text)}"
    + ".pp-msearch-cancel{border:none;background:transparent;font-family:var(--pp-font);font-size:15px;font-weight:600;color:var(--c-accent-text);cursor:pointer;padding:8px 4px;flex:none}"
    + ".pp-msearch-body{flex:1;overflow-y:auto;padding:20px 16px}"
    + ".pp-msearch-label{display:block;font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--c-text-faint);margin-bottom:12px}"
    + ".pp-msearch-recent{display:flex;flex-direction:column;gap:2px;margin-bottom:26px}"
    + ".pp-msearch-row{display:flex;align-items:center;gap:12px;padding:13px 8px;border:none;background:transparent;font-family:var(--pp-font);font-size:16px;color:var(--c-text);cursor:pointer;text-align:left;width:100%;text-decoration:none}"
    + ".pp-msearch-row svg{flex:none;color:var(--c-icon)}"
    // ---- full-screen mobile menu ----
    + ".pp-menu{position:fixed;inset:0;z-index:var(--pp-z-menu,21);background:var(--c-bg);display:none;flex-direction:column}"
    + ".pp-menu.-open{display:flex}"
    + ".pp-menu-head{height:56px;padding:0 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--c-border);flex:none}"
    + "@media(min-width:768px){.pp-menu-head{height:60px}}"
    + ".pp-menu-head img{width:26px;height:26px;display:block}"
    + ".pp-menu-body{flex:1;overflow-y:auto;padding:12px 16px 24px}"
    + "@media(min-width:768px){.pp-menu-body{padding:16px 24px 24px}}"
    + ".pp-menu-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 4px;font-size:17.5px;font-weight:500;color:var(--c-text);border-bottom:1px solid var(--c-surface-inset);text-decoration:none}"
    + "@media(min-width:768px){.pp-menu-row{font-size:19px;padding:16px 4px}}"
    + ".pp-menu-row:hover{color:var(--c-accent-text)}"
    + ".pp-menu-row.-active{color:var(--c-accent-text);font-weight:600}"
    + ".pp-menu-row svg{flex:none;color:var(--c-icon)}"
    + ".pp-menu-caption{display:block;font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--c-text-faint);margin:24px 0 6px}"
    + ".pp-menu-foot{flex:none;padding:14px 16px 18px;border-top:1px solid var(--c-border);display:flex;gap:10px}"
    + ".pp-menu-foot .pp-btn-ghost,.pp-menu-foot .pp-btn-gold{flex:1;justify-content:center;height:48px}"
    + ".pp-menu-user-wrap{flex:none;border-top:1px solid var(--c-border)}"
    + ".pp-menu-user{padding:12px 16px 4px;display:flex;align-items:center;gap:11px;text-decoration:none;color:var(--c-text)}"
    + ".pp-menu-user .pp-av-circle{width:40px;height:40px;font-size:14px}"
    + ".pp-menu-user-name{font-size:15px;font-weight:700}"
    + ".pp-menu-user-sub{font-size:12.5px;color:var(--c-accent-text)}"
    // Ink, not red — sign out is disruptive but recoverable, and only
    // genuine account loss (Close account) takes --c-bad-text per house rules.
    + ".pp-menu-signout{display:block;width:calc(100% - 32px);margin:6px 16px 18px;padding:12px 4px;border:none;"
    + "background:none;font:inherit;font-size:14.5px;font-weight:600;color:var(--c-text);text-align:left;cursor:pointer}"
    // ---- focused-flow shell (sign in/up, reset, onboarding) ----
    + ".pp-header-focused{padding:32px var(--pp-page-margin) 0}"
    + ".pp-header-focused a{display:inline-flex}"
    + ".pp-header-focused img{width:32px;height:32px;display:block}"
    // ---- breadcrumbs ----
    // body is display:flex;flex-direction:column (see base reset in every
    // pp-*.html page), and #pp-breadcrumbs is a direct child of body that's
    // itself display:flex — a flex item that's also a flex container sizes
    // to fit its own content on the cross axis instead of stretching to
    // fill, so without an explicit width the whole bar (and its centering
    // margin:auto) collapses to hug the breadcrumb text instead of the
    // page's actual content width, and every page's back-to-page-content
    // alignment breaks (this is what "align to left" was reporting).
    + ".pp-crumbs{width:100%;max-width:var(--pp-container);margin:0 auto;padding:var(--pp-page-margin);padding-top:16px;padding-bottom:0;display:flex;align-items:center;gap:9px;font-size:14px;color:var(--c-text-subtle);position:sticky;top:56px;z-index:var(--pp-z-sticky,2);background:var(--c-bg)}"
    + "@media(min-width:1024px){.pp-crumbs{top:var(--pp-header-height);position:static}}"
    + ".pp-crumbs a{color:var(--c-text-subtle);text-decoration:none}"
    + ".pp-crumbs a:hover{color:var(--c-text)}"
    + ".pp-crumbs .-current{color:var(--c-text);font-weight:600}"
    + ".pp-crumbs svg{flex:none;color:var(--c-border-strong)}"
    + ".pp-crumbs-back{display:flex;align-items:center;color:var(--c-text)}"
    + "@media(min-width:640px){.pp-crumbs-back{display:none}}"
    + ".pp-crumbs-full{display:none;align-items:center;gap:9px}"
    + "@media(min-width:640px){.pp-crumbs-full{display:flex}}";

  function injectStyle(){
    if (document.getElementById("pp-nav-style")) return;
    var s = document.createElement("style");
    s.id = "pp-nav-style";
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function navLinkHtml(item, active){
    return '<a href="' + item.href + '" class="' + (active ? "-active" : "") + '">' + item.label + '</a>';
  }

  function menuRowHtml(item, active){
    return '<a href="' + item.href + '" class="pp-menu-row' + (active ? " -active" : "") + '">' +
      '<span>' + item.label + '</span>' + ICONS.chevronRight(19) + '</a>';
  }

  function initialsOf(user){
    return user.initials || (user.name || "").split(" ").map(function(w){return w[0];}).slice(0,2).join("").toUpperCase();
  }

  // pp-nav.js is meant to be self-contained (one <script> tag, see the
  // header comment) — no page actually populates data-user, so this is the
  // only source of signed-in state: read the same token pp-dashboard.html
  // etc. already store, and ask the API who that is.
  function resolveSignedInUser(cb){
    var token;
    try { token = localStorage.getItem(DESIGNER_TOKEN_KEY); } catch (e) { token = null; }
    if (!token) { cb(null); return; }
    fetch("/api/designers/me", { headers: { "Authorization": "Bearer " + token } })
      .then(function(res){
        if (!res.ok) {
          if (res.status === 401) { try { localStorage.removeItem(DESIGNER_TOKEN_KEY); } catch (e) {} }
          return null;
        }
        return res.json();
      })
      .then(function(me){
        cb(me ? { name: me.display_name, initials: initialsOf({ name: me.display_name }), href: "/designers/" + encodeURIComponent(me.handle || me.id) } : null);
      })
      .catch(function(){ cb(null); });
  }

  function ensureConfirmLoaded(cb){
    if (window.showPPConfirm) { cb(); return; }
    var s = document.createElement("script");
    s.src = "/pp-confirm.js";
    s.onload = cb;
    s.onerror = cb; // doSignOut() falls back to a plain confirm() if this never defines showPPConfirm
    document.head.appendChild(s);
  }

  // kind: "designer" (default, used by this file's own avatar menu) or
  // "employer" (used by pp-employer.html's dashboard rail) — same flow,
  // different token key and logout endpoint. Exposed on PathPixelNav so
  // both dashboards can call it instead of duplicating the confirm +
  // logout-call + token-clear sequence.
  function doSignOut(kind){
    kind = kind === "employer" ? "employer" : "designer";
    var tokenKey = kind === "employer" ? "kazi_employer_token" : DESIGNER_TOKEN_KEY;
    var logoutUrl = kind === "employer" ? "/api/employers/logout" : "/api/designers/logout";
    ensureConfirmLoaded(function(){
      var proceed = window.showPPConfirm
        ? window.showPPConfirm({
            title: "Sign out?",
            description: "You'll need to sign back in to see your saved roles and messages.",
            confirmLabel: "Sign out", safeLabel: "Stay signed in", danger: false
          })
        : Promise.resolve(confirm("Sign out?"));
      proceed.then(function(ok){
        if (!ok) return;
        var token;
        try { token = localStorage.getItem(tokenKey); } catch (e) { token = null; }
        var done = token
          ? fetch(logoutUrl, { method: "POST", headers: { "Authorization": "Bearer " + token } }).catch(function(){})
          : Promise.resolve();
        done.then(function(){
          try { localStorage.removeItem(tokenKey); } catch (e) {}
          location.href = "/";
        });
      });
    });
  }

  function authControlsHtml(user){
    if (user) {
      return '<div class="pp-avatar-menu" data-avatar-menu>' +
        '<button type="button" class="pp-avatar-chip" data-avatar-trigger aria-haspopup="true" aria-expanded="false">' +
          '<span class="pp-av-name">' + user.name + '</span>' +
          '<span class="pp-av-circle">' + initialsOf(user) + '</span></button>' +
        '<div class="pp-avatar-dropdown" data-avatar-dropdown>' +
          '<a href="' + (user.href || "#") + '">My profile</a>' +
          '<button type="button" data-sign-out>Sign out</button>' +
        '</div>' +
      '</div>';
    }
    return '<div class="pp-auth">' +
      '<a class="pp-btn-ghost" href="' + ROUTES.signin + '">Sign in</a>' +
      '<a class="pp-btn-gold" href="' + ROUTES.join + '">Join for free</a></div>';
  }

  function recentSearches(){
    try { return JSON.parse(localStorage.getItem("pp_recent_searches") || "[]"); }
    catch (e) { return []; }
  }
  function saveRecentSearch(q){
    if (!q) return;
    var list = recentSearches().filter(function(x){ return x.toLowerCase() !== q.toLowerCase(); });
    list.unshift(q);
    try { localStorage.setItem("pp_recent_searches", JSON.stringify(list.slice(0, 5))); } catch (e) {}
  }

  function buildFullHeader(root, active, user){
    var dlinks = NAV_ITEMS.map(function(i){ return navLinkHtml(i, i.key === active); }).join("");
    var mlinks = NAV_ITEMS.map(function(i){ return menuRowHtml(i, i.key === active); }).join("");
    var recent = recentSearches();
    var recentChips = recent.map(function(q, idx){
      return '<button type="button" class="pp-recent-chip" data-recent="' + idx + '">' + q + '</button>';
    }).join("");
    var recentRows = recent.map(function(q, idx){
      return '<button type="button" class="pp-msearch-row" data-recent="' + idx + '">' + ICONS.clock(17) + q + '</button>';
    }).join("");

    root.innerHTML =
      '<header class="pp-header">' +
        '<div class="pp-header-row">' +
          '<a class="pp-mark" href="' + ROUTES.home + '"><img src="/brand/pp-icon.png" alt="Path &amp; Pixel"></a>' +
          '<nav class="pp-dlinks">' + dlinks + '</nav>' +
          '<div class="pp-spacer"></div>' +
          '<button type="button" class="pp-icon-btn" data-open-search aria-label="Search Path &amp; Pixel">' + ICONS.search(18) + '</button>' +
          '<a class="pp-post-link" href="' + ROUTES.postRole + '">Post a role</a>' +
          '<button type="button" class="pp-theme-btn" data-theme-toggle aria-label="Toggle dark mode" title="Toggle dark mode">' + ICONS.theme(18) + '</button>' +
          authControlsHtml(user) +
        '</div>' +
        '<div class="pp-search-drop" data-search-drop>' +
          '<div class="pp-search-inner">' +
            '<div class="pp-search-field" data-search-field>' + ICONS.search(18) +
              '<input type="text" data-search-input placeholder="Roles, designers, companies…">' +
              '<button type="button" class="pp-search-clear" data-clear-search hidden aria-label="Clear search">' + ICONS.clear(13) + '</button>' +
              '<button type="button" class="pp-search-esc" data-close-search>Esc</button>' +
            '</div>' +
            '<div class="pp-recent-row" data-recent-row' + (recent.length ? '' : ' hidden') + '><span>Recent</span>' + recentChips + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="pp-header-compact">' +
          '<a class="pp-mark" href="' + ROUTES.home + '"><img src="/brand/pp-icon.png" alt="Path &amp; Pixel"></a>' +
          '<div class="pp-spacer"></div>' +
          '<button type="button" class="pp-tap-btn" data-open-msearch aria-label="Search">' + ICONS.search(19) + '</button>' +
          (user ? '<a class="pp-av-circle" href="' + user.href + '" style="width:30px;height:30px;font-size:11px;text-decoration:none">' +
            (user.initials || "") + '</a>' : '') +
          '<button type="button" class="pp-tap-theme" data-theme-toggle aria-label="Toggle dark mode" title="Toggle dark mode">' + ICONS.theme(19) + '</button>' +
          '<button type="button" class="pp-tap-btn pp-menu-btn" data-open-menu aria-label="Menu">' + ICONS.hamburger(22) + '</button>' +
        '</div>' +
      '</header>' +
      '<div class="pp-msearch" data-msearch>' +
        '<div class="pp-msearch-head">' +
          '<div class="pp-msearch-field" data-msearch-field>' + ICONS.search(17) +
            '<input type="text" data-msearch-input placeholder="Roles, designers, companies…">' +
            '<button type="button" class="pp-search-clear" data-clear-msearch hidden aria-label="Clear search">' + ICONS.clear(12) + '</button>' +
          '</div>' +
          '<button type="button" class="pp-msearch-cancel" data-close-msearch>Cancel</button>' +
        '</div>' +
        '<div class="pp-msearch-body">' +
          '<div data-msearch-recent-wrap' + (recent.length ? '' : ' hidden') + '>' +
            '<span class="pp-msearch-label">Recent</span>' +
            '<div class="pp-msearch-recent" data-msearch-recent>' + recentRows + '</div>' +
          '</div>' +
          '<span class="pp-msearch-label">Jump to</span>' +
          '<div>' +
            '<a class="pp-msearch-row" href="' + ROUTES.jobs + '">' + navIcon(NAV_ITEMS[1], 18) + 'Browse all roles</a>' +
            '<a class="pp-msearch-row" href="' + ROUTES.people + '">' + navIcon(NAV_ITEMS[2], 18) + 'Browse designers</a>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="pp-menu" data-menu>' +
        '<div class="pp-menu-head">' +
          '<a class="pp-mark" href="' + ROUTES.home + '"><img src="/brand/pp-icon.png" alt="Path &amp; Pixel"></a>' +
          '<div class="pp-spacer"></div>' +
          '<button type="button" class="pp-tap-btn" data-close-menu aria-label="Close menu">' + ICONS.close(20) + '</button>' +
        '</div>' +
        '<div class="pp-menu-body">' +
          mlinks +
          '<span class="pp-menu-caption">Your things</span>' +
          menuRowHtml({ label: "Saved roles", href: "/dashboard" }, false) +
          menuRowHtml({ label: "Résumé check", href: ROUTES.resources }, false) +
          menuRowHtml({ label: "Post a role", href: ROUTES.postRole }, false) +
        '</div>' +
        (user
          ? '<div class="pp-menu-user-wrap">' +
              '<a class="pp-menu-user" href="' + user.href + '">' +
                '<span class="pp-av-circle">' + initialsOf(user) + '</span>' +
                '<span style="flex:1;min-width:0"><span class="pp-menu-user-name" style="display:block">' + user.name + '</span>' +
                '<span class="pp-menu-user-sub">View your profile</span></span>' + ICONS.chevronRight(18) +
              '</a>' +
              '<button type="button" class="pp-menu-signout" data-sign-out>Sign out</button>' +
            '</div>'
          : '<div class="pp-menu-foot">' +
              '<a class="pp-btn-ghost" href="' + ROUTES.signin + '">Sign in</a>' +
              '<a class="pp-btn-gold" href="' + ROUTES.join + '">Join for free</a></div>') +
      '</div>';

    wireInteractions(root);
  }

  function buildFocusedHeader(root){
    root.innerHTML =
      '<div class="pp-header-focused">' +
        '<a href="' + ROUTES.home + '"><img src="/brand/pp-icon.png" alt="Path &amp; Pixel"></a>' +
      '</div>';
  }

  function wireInteractions(root){
    var searchDrop = root.querySelector("[data-search-drop]");
    var searchField = root.querySelector("[data-search-field]");
    var searchInput = root.querySelector("[data-search-input]");
    var clearSearch = root.querySelector("[data-clear-search]");
    var msearch = root.querySelector("[data-msearch]");
    var msearchField = root.querySelector("[data-msearch-field]");
    var msearchInput = root.querySelector("[data-msearch-input]");
    var clearMsearch = root.querySelector("[data-clear-msearch]");
    var menu = root.querySelector("[data-menu]");

    function openSearch(){
      searchDrop.classList.add("-open");
      setTimeout(function(){ searchInput.focus(); }, 10);
    }
    function closeSearch(){ searchDrop.classList.remove("-open"); }

    root.querySelector("[data-open-search]").addEventListener("click", openSearch);
    root.querySelector("[data-close-search]").addEventListener("click", closeSearch);
    searchInput.addEventListener("focus", function(){ searchField.classList.add("-focus"); });
    searchInput.addEventListener("blur", function(){ searchField.classList.remove("-focus"); });
    searchInput.addEventListener("input", function(){
      clearSearch.hidden = !searchInput.value;
    });
    searchInput.addEventListener("keydown", function(e){
      if (e.key === "Escape") closeSearch();
      if (e.key === "Enter" && searchInput.value.trim()) saveRecentSearch(searchInput.value.trim());
    });
    clearSearch.addEventListener("click", function(){
      searchInput.value = "";
      clearSearch.hidden = true;
      searchInput.focus();
    });
    root.querySelectorAll("[data-recent]").forEach(function(btn){
      btn.addEventListener("click", function(){
        searchInput.value = btn.textContent;
        clearSearch.hidden = false;
        searchInput.focus();
      });
    });

    function openMSearch(){
      msearch.classList.add("-open");
      setTimeout(function(){ msearchInput.focus(); }, 10);
    }
    function closeMSearch(){ msearch.classList.remove("-open"); }
    root.querySelector("[data-open-msearch]").addEventListener("click", openMSearch);
    root.querySelector("[data-close-msearch]").addEventListener("click", closeMSearch);
    msearchInput.addEventListener("focus", function(){ msearchField.classList.add("-focus"); });
    msearchInput.addEventListener("blur", function(){ msearchField.classList.remove("-focus"); });
    msearchInput.addEventListener("input", function(){ clearMsearch.hidden = !msearchInput.value; });
    clearMsearch.addEventListener("click", function(){
      msearchInput.value = "";
      clearMsearch.hidden = true;
      msearchInput.focus();
    });

    function openMenu(){ menu.classList.add("-open"); document.body.style.overflow = "hidden"; }
    function closeMenu(){ menu.classList.remove("-open"); document.body.style.overflow = ""; }
    root.querySelector("[data-open-menu]").addEventListener("click", openMenu);
    root.querySelector("[data-close-menu]").addEventListener("click", closeMenu);

    root.querySelectorAll("[data-theme-toggle]").forEach(function(btn){
      btn.addEventListener("click", function(){
        var root2 = document.documentElement;
        var next = root2.getAttribute("data-pp-theme") === "dark" ? "light" : "dark";
        root2.setAttribute("data-pp-theme", next);
        try { localStorage.setItem("pp_theme", next); } catch (e) {}
      });
    });

    var avatarMenu = root.querySelector("[data-avatar-menu]");
    if (avatarMenu) {
      var avatarTrigger = root.querySelector("[data-avatar-trigger]");
      var avatarDropdown = root.querySelector("[data-avatar-dropdown]");
      avatarTrigger.addEventListener("click", function(e){
        e.stopPropagation();
        var open = avatarDropdown.classList.toggle("-open");
        avatarTrigger.setAttribute("aria-expanded", open ? "true" : "false");
      });
      document.addEventListener("click", function(e){
        if (!avatarMenu.contains(e.target)) {
          avatarDropdown.classList.remove("-open");
          avatarTrigger.setAttribute("aria-expanded", "false");
        }
      });
    }
    root.querySelectorAll("[data-sign-out]").forEach(function(btn){
      btn.addEventListener("click", function(){ doSignOut("designer"); });
    });
  }

  function buildBreadcrumbs(el){
    var crumbs;
    try { crumbs = JSON.parse(el.getAttribute("data-crumbs") || "[]"); } catch (e) { crumbs = []; }
    if (!crumbs.length) return;
    var all = [{ label: "Home", href: ROUTES.home }].concat(crumbs);
    if (all.length > 3) all = [all[0], all[all.length - 2], all[all.length - 1]];
    var current = all[all.length - 1];
    var parent = all.length > 1 ? all[all.length - 2] : all[0];

    var full = all.map(function(c, idx){
      var isLast = idx === all.length - 1;
      var sep = idx > 0 ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"></path></svg>' : "";
      var item = isLast
        ? '<span class="-current">' + c.label + '</span>'
        : '<a href="' + (c.href || "#") + '">' + c.label + '</a>';
      return sep + item;
    }).join("");

    el.className = "pp-crumbs";
    el.innerHTML =
      '<a class="pp-crumbs-back" href="' + (parent.href || ROUTES.home) + '" aria-label="Back to ' + parent.label + '">' +
        ICONS.back(18) + '<span style="margin-left:6px">' + parent.label + '</span>' +
      '</a>' +
      '<span class="pp-crumbs-full">' + full + '</span>';
  }

  // Renders the nav into a given element, reading options from its
  // data-* attributes (real page usage) or from an explicit opts object
  // (programmatic use, e.g. a preview harness mounting more than one
  // instance on a page, which data-* attribute discovery can't do since
  // element ids must be unique).
  function render(el, opts){
    opts = opts || {};
    var shell = opts.shell || el.getAttribute("data-shell") || "full";
    var active = opts.active || el.getAttribute("data-active") || "";
    if (shell === "focused") { buildFocusedHeader(el); return; }

    var explicitUser = opts.user;
    if (explicitUser === undefined) {
      var attr = el.getAttribute("data-user");
      explicitUser = attr ? (function(){ try { return JSON.parse(attr); } catch (e) { return null; } })() : undefined;
    }
    if (explicitUser !== undefined) { buildFullHeader(el, active, explicitUser); return; }

    // No caller-supplied state (the common case — no page sets data-user):
    // paint signed-out immediately so the header never sits blank, then
    // swap in the real state once the token check resolves.
    buildFullHeader(el, active, null);
    resolveSignedInUser(function(user){
      if (user) buildFullHeader(el, active, user);
    });
  }

  function renderBreadcrumbs(el, crumbs){
    if (crumbs !== undefined) el.setAttribute("data-crumbs", JSON.stringify(crumbs));
    buildBreadcrumbs(el);
  }

  function init(){
    injectStyle();
    try {
      var savedTheme = localStorage.getItem("pp_theme");
      if (savedTheme) document.documentElement.setAttribute("data-pp-theme", savedTheme);
      else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
        // First visit, no explicit choice yet: follow the OS. Deliberately
        // not persisted to localStorage, so it keeps tracking the system
        // setting until the visitor makes an explicit choice via the toggle.
        document.documentElement.setAttribute("data-pp-theme", "dark");
      }
    } catch (e) {}

    var navRoot = document.getElementById("pp-nav");
    if (navRoot) render(navRoot);

    var crumbsEl = document.getElementById("pp-breadcrumbs");
    if (crumbsEl) buildBreadcrumbs(crumbsEl);
  }

  window.PathPixelNav = { ROUTES: ROUTES, render: render, renderBreadcrumbs: renderBreadcrumbs, injectStyle: injectStyle, signOut: doSignOut };
  ready(init);
})();
