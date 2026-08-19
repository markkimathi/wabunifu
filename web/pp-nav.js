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

  /* Scrolling that actually happens.

     Smooth scrolling is silently a no-op in WebKit-based embedded webviews —
     both window.scrollTo({behavior:"smooth"}) and Element.scrollIntoView with
     it. That is not an edge case for this product: a shared role link opened
     inside the WhatsApp or Instagram browser is one of the main ways people
     arrive, and there the page simply never moved. Pagination looked broken,
     and a wizard step change left you halfway down the previous step.

     So: ask for smooth, then check. If nothing moved by the next frames, do it
     instantly. Reduced-motion users skip straight to instant. */
  function scrollDone(before, apply, target){
    var moved = function(){ return Math.abs(window.pageYOffset - before) > 1; };
    setTimeout(function(){ if (!moved()) target(); }, 120);
  }
  function reduceMotion(){
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }
  var PPScroll = {
    toTop: function(){
      if (reduceMotion()) { window.scrollTo(0, 0); return; }
      var before = window.pageYOffset;
      if (before === 0) return;
      try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch (e) { window.scrollTo(0, 0); }
      scrollDone(before, null, function(){ window.scrollTo(0, 0); });
    },
    intoView: function(el, block){
      if (!el) return;
      var opts = { block: block || "start" };
      if (reduceMotion()) { el.scrollIntoView(opts); return; }
      var before = window.pageYOffset;
      try { el.scrollIntoView({ block: opts.block, behavior: "smooth" }); }
      catch (e) { el.scrollIntoView(opts); }
      scrollDone(before, null, function(){ el.scrollIntoView(opts); });
    }
  };
  window.PPScroll = PPScroll;

  /* The filter panel that becomes an overlay below 1024px — a bottom sheet on
     mobile, a right-hand drawer at tablet portrait. It covers the page and
     locks scrolling behind it, so it is a modal dialog and has to behave like
     one. It did not: Escape didn't close it, focus never moved into it, and
     nothing stopped Tab walking off into the page hidden behind the backdrop.

     Wired here rather than in each page because /jobs and /people had the same
     four lines copied, and would have grown the same four bugs twice. */
  function PPSheet(opts){
    var panel = opts.panel, toggle = opts.toggle, backdrop = opts.backdrop, closeBtn = opts.closeBtn;
    if (!panel || !toggle) return null;
    var lastFocus = null;

    function overlayMode(){
      // Above the breakpoint the panel is a plain sidebar, not a dialog, and
      // must not claim any of the modal semantics.
      return window.matchMedia("(max-width: 1023px)").matches;
    }
    function focusables(){
      return [].slice.call(panel.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]),' +
        ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter(function(el){ return el.offsetParent !== null; });
    }
    function open(){
      lastFocus = document.activeElement;
      panel.classList.add("-open");
      if (backdrop) backdrop.classList.add("-open");
      document.body.style.overflow = "hidden";
      toggle.setAttribute("aria-expanded", "true");
      if (overlayMode()) {
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-modal", "true");
      }
      var first = closeBtn || focusables()[0];
      if (first && first.focus) first.focus();
    }
    function close(){
      panel.classList.remove("-open");
      if (backdrop) backdrop.classList.remove("-open");
      document.body.style.overflow = "";
      toggle.setAttribute("aria-expanded", "false");
      panel.removeAttribute("role");
      panel.removeAttribute("aria-modal");
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    function isOpen(){ return panel.classList.contains("-open"); }

    if (!panel.id) panel.id = "pp-sheet-" + Math.random().toString(36).slice(2, 8);
    toggle.setAttribute("aria-controls", panel.id);
    toggle.setAttribute("aria-expanded", "false");

    toggle.addEventListener("click", function(){ isOpen() ? close() : open(); });
    if (closeBtn) closeBtn.addEventListener("click", close);
    if (backdrop) backdrop.addEventListener("click", close);

    document.addEventListener("keydown", function(e){
      if (!isOpen()) return;
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key !== "Tab" || !overlayMode()) return;
      var items = focusables();
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      else if (!panel.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    });

    // Dragged back up to desktop with the sheet open: it is a sidebar again, so
    // drop the lock and the dialog semantics rather than trapping the page.
    window.addEventListener("resize", function(){
      if (isOpen() && !overlayMode()) close();
    });

    return { open: open, close: close, isOpen: isOpen };
  }
  window.PPSheet = PPSheet;

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
    bell: function(s){ return svg(s, '<path d="M18 8a6 6 0 10-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"></path><path d="M13.7 21a2 2 0 01-3.4 0"></path>'); },
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
    // Same trap, same fix: .pp-bell-dot carries its own display, so the
    // hidden attribute alone left a "0" badge sitting on the bell.
    + ".pp-bell-dot[hidden],.pp-notif-panel[hidden],.pp-sresults[hidden],.pp-tab-dot[hidden]{display:none!important}"
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
    + ".pp-bell{position:relative;flex:none;width:38px;height:38px;display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--c-border);border-radius:999px;background:transparent;color:var(--c-text-muted);cursor:pointer}"
    + ".pp-bell:hover{background:var(--c-bg-sunken);color:var(--c-text)}"
    + ".pp-bell-dot{position:absolute;top:6px;right:6px;min-width:16px;height:16px;padding:0 4px;border-radius:999px;background:var(--pp-gold);color:var(--pp-ink);font-size:10px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;line-height:1}"
    + ".pp-notif-panel{position:absolute;top:calc(100% + 8px);right:0;width:min(360px,calc(100vw - 32px));max-height:60vh;overflow-y:auto;z-index:var(--pp-z-menu,21);"
    +   "background:var(--c-surface);border:1px solid var(--c-border);border-radius:var(--pp-radius-lg);box-shadow:var(--pp-shadow-lg);padding:6px}"
    + ".pp-notif-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 10px 6px}"
    + ".pp-notif-head span{font-size:13px;font-weight:700}"
    + ".pp-notif-head button{border:none;background:none;padding:0;cursor:pointer;font-family:inherit;font-size:12.5px;font-weight:600;color:var(--c-accent-text)}"
    + ".pp-notif-item{display:block;width:100%;text-align:left;padding:10px;border:none;border-radius:var(--pp-radius-md);background:transparent;cursor:pointer;font-family:inherit;text-decoration:none;color:inherit}"
    + ".pp-notif-item:hover{background:var(--c-bg-sunken)}"
    + ".pp-notif-item.-unread{background:var(--pp-gold-200)}"
    + ".pp-notif-item .t{display:block;font-size:13.5px;font-weight:600;color:var(--c-text);margin-bottom:2px}"
    + ".pp-notif-item .b{display:block;font-size:12.5px;line-height:1.45;color:var(--c-text-subtle)}"
    + ".pp-notif-empty{padding:26px 14px;text-align:center;font-size:13px;color:var(--c-text-subtle)}"
    + ".pp-avatar-menu{position:relative;flex:none}"
    + ".pp-avatar-chip{display:inline-flex;align-items:center;gap:9px;flex:none;padding:4px 4px 4px 12px;border-radius:999px;border:1px solid var(--c-border);background:transparent;text-decoration:none;color:var(--c-text);font:inherit;cursor:pointer}"
    + ".pp-avatar-chip:hover{background:var(--c-bg-sunken)}"
    + ".pp-avatar-chip .pp-av-name{font-size:14px;font-weight:500}"
    + ".pp-av-circle{position:relative;overflow:hidden;flex:none;width:32px;height:32px;border-radius:50%;background:var(--pp-gold-300);display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--pp-gold-800)}"
    // Sits over the initials rather than replacing them, so onerror can drop
    // the image and reveal the letters underneath.
    + ".pp-av-circle img{position:absolute;inset:0;width:100%;height:100%;border-radius:50%;object-fit:cover;display:block}"
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
    + ".pp-sresults{margin-top:14px;display:flex;flex-direction:column;gap:14px}"
    + ".pp-sgroup-label{display:block;font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--c-text-faint);margin-bottom:6px}"
    + ".pp-sitem{display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:var(--pp-radius-md);text-decoration:none;color:inherit}"
    + ".pp-sitem:hover{background:var(--c-bg-sunken)}"
    + ".pp-sitem .pp-sthumb{width:32px;height:32px;flex:none;border-radius:var(--pp-radius-sm);object-fit:cover;background:var(--c-surface-inset);display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--c-text-subtle)}"
    + ".pp-sitem .pp-sthumb.-round{border-radius:50%}"
    + ".pp-sitem .pp-stext{flex:1;min-width:0}"
    + ".pp-sitem .pp-st{display:block;font-size:14px;font-weight:600;color:var(--c-text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    + ".pp-sitem .pp-ss{display:block;font-size:12.5px;color:var(--c-text-subtle);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    + ".pp-sempty{padding:18px 10px;font-size:13.5px;color:var(--c-text-subtle)}"
    + ".pp-sall{display:inline-block;margin-top:2px;font-size:13px;font-weight:600;color:var(--c-accent-text);text-decoration:none;padding:6px 10px}"
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
    // ---- bottom tab bar (signed-in designers, below 1024px) ----
    // A hamburger costs a tap for the four things people do most, and on a
    // job board read mostly on phones that is the whole session. The menu
    // stays for everything else; this is only the frequent path.
    + ".pp-tabbar{position:fixed;left:0;right:0;bottom:0;z-index:var(--pp-z-header,6);display:none;"
    +   "background:var(--c-bg);border-top:1px solid var(--c-border);"
    +   "padding-bottom:env(safe-area-inset-bottom,0px)}"
    + "@media(max-width:1023px){.pp-tabbar{display:flex}}"
    + ".pp-tabbar a{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;"
    +   "gap:3px;height:56px;text-decoration:none;color:var(--c-text-faint);position:relative}"
    + ".pp-tabbar a.-active{color:var(--c-accent-text)}"
    + ".pp-tabbar .pp-tab-label{font-size:11px;font-weight:600;letter-spacing:-.01em}"
    + ".pp-tabbar .pp-tab-dot{position:absolute;top:8px;left:50%;margin-left:6px;width:7px;height:7px;"
    +   "border-radius:50%;background:var(--pp-gold)}"
    // The bar floats over the page, so the last thing on every page would sit
    // under it. Scoped to a class set only when the bar actually renders —
    // reserving the space for signed-out visitors would leave a dead strip
    // under every page they see.
    + "@media(max-width:1023px){body.pp-has-tabbar{padding-bottom:56px}}"
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
    // No horizontal padding here on purpose: every page's .hero (the
    // element the breadcrumb trail sits directly above) zeroes its own
    // left/right padding via a 3-value `padding:Ypx 0 0` shorthand on
    // .wrap.hero, so page content already sits flush against .wrap's own
    // edge rather than inset by --pp-page-margin. Giving the breadcrumb
    // horizontal padding put it out of step with the heading right below
    // it instead of sharing its left edge.
    + ".pp-crumbs{width:100%;max-width:var(--pp-container);margin:0 auto;padding:16px 0 0;display:flex;align-items:center;gap:9px;font-size:14px;color:var(--c-text-subtle);position:sticky;top:56px;z-index:var(--pp-z-sticky,2);background:var(--c-bg)}"
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

  // Everyone has a picture: either one they uploaded or the built-in avatar
  // they picked, and avatar-1.png when they've set neither (the backend stores
  // whatever was last set and leaves this fallback to the frontend — see
  // DEFAULT_AVATAR_PATH in api/main.py). Initials are only the last resort for
  // an image that fails to load.
  // The initials stay in the markup underneath the image, so a picture that
  // fails to load drops back to them instead of an empty gold disc.
  var DEFAULT_AVATAR = "/avatars/avatar-1.png";
  function avatarInnerHtml(user){
    var initials = initialsOf(user);
    if (!user.photo) return initials;
    return initials + '<img src="' + user.photo + '" alt="" onerror="this.onerror=null;this.remove()">';
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
        cb(me ? { name: me.display_name, initials: initialsOf({ name: me.display_name }),
                  photo: me.photo_path || DEFAULT_AVATAR,
                  href: "/designers/" + encodeURIComponent(me.handle || me.id) } : null);
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


  /* Live results for the header search, which until now saved your term to a
     "recent" list and did nothing else — a control that looked functional on
     every page and wasn't.

     Grouped by type because roles, people and companies are different intents;
     a blended ranked list makes the reader do the sorting. Debounced so typing
     doesn't fire a request per keystroke, and every render is guarded by the
     term it was for, so a slow response can't overwrite a newer one. */
  function wireLiveSearch(root){
    var boxes = Array.prototype.slice.call(root.querySelectorAll("[data-search-results]"));
    if (!boxes.length) return;
    var timer = null;
    var lastRendered = "";

    function esc(t){
      return String(t == null ? "" : t).replace(/[&<>"']/g, function(c){
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }
    function initials(name){
      return String(name || "?").trim().split(/\s+/).map(function(w){ return w[0]; }).slice(0,2).join("").toUpperCase();
    }
    function thumb(src, name, round){
      if (src) return '<img class="pp-sthumb' + (round ? " -round" : "") + '" src="' + esc(src) + '" alt="">';
      return '<span class="pp-sthumb' + (round ? " -round" : "") + '">' + esc(initials(name)) + '</span>';
    }
    function group(label, items, render){
      if (!items.length) return "";
      return '<div><span class="pp-sgroup-label">' + label + '</span>' + items.map(render).join("") + '</div>';
    }

    function paint(html){
      boxes.forEach(function(b){ b.innerHTML = html; b.hidden = !html; });
    }

    function clear(){
      lastRendered = "";
      paint("");
      root.querySelectorAll("[data-recent-row],[data-msearch-recent-wrap]").forEach(function(el){
        if (el.getAttribute("data-had-recent") === "1") el.hidden = false;
      });
    }

    async function run(term){
      if (term.length < 2) { clear(); return; }
      try {
        var res = await fetch("/api/search?q=" + encodeURIComponent(term));
        if (!res.ok) return;
        var d = await res.json();
        // A response for a term the user has already moved past must not win.
        if ((d.query || "").trim().toLowerCase() !== term.toLowerCase()) return;
        lastRendered = term;

        // Recent is for an empty box; once there are results it is in the way.
        root.querySelectorAll("[data-recent-row],[data-msearch-recent-wrap]").forEach(function(el){
          if (!el.hasAttribute("data-had-recent")) el.setAttribute("data-had-recent", el.hidden ? "0" : "1");
          el.hidden = true;
        });

        if (!d.total) {
          paint('<p class="pp-sempty">Nothing matches “' + esc(term) + '” yet. ' +
                '<a class="pp-sall" href="/jobs?q=' + encodeURIComponent(term) + '">Search the whole board →</a></p>');
          return;
        }
        paint(
          group("Roles", d.roles, function(r){
            return '<a class="pp-sitem" href="' + esc(r.href) + '">' + thumb("", r.company) +
              '<span class="pp-stext"><span class="pp-st">' + esc(r.title) + '</span>' +
              '<span class="pp-ss">' + esc(r.company) + ' · ' + esc(r.place) + '</span></span></a>';
          }) +
          group("People", d.people, function(p){
            return '<a class="pp-sitem" href="' + esc(p.href) + '">' + thumb(p.photo, p.name, true) +
              '<span class="pp-stext"><span class="pp-st">' + esc(p.name) + '</span>' +
              '<span class="pp-ss">' + esc(p.sub) + '</span></span></a>';
          }) +
          group("Companies", d.companies, function(c){
            return '<a class="pp-sitem" href="' + esc(c.href) + '">' + thumb(c.logo, c.name) +
              '<span class="pp-stext"><span class="pp-st">' + esc(c.name) + '</span>' +
              (c.sub ? '<span class="pp-ss">' + esc(c.sub) + '</span>' : "") + '</span></a>';
          }) +
          '<a class="pp-sall" href="/jobs?q=' + encodeURIComponent(term) + '">See all roles for “' + esc(term) + '” →</a>'
        );
      } catch (e) {}
    }

    root.querySelectorAll("[data-search-input],[data-msearch-input]").forEach(function(input){
      input.addEventListener("input", function(){
        var term = input.value.trim();
        clearTimeout(timer);
        timer = setTimeout(function(){ run(term); }, 220);
      });
      // Enter now goes somewhere. The board is the honest destination: it is
      // the only surface that can hold a full result set.
      input.addEventListener("keydown", function(e){
        if (e.key !== "Enter") return;
        var term = input.value.trim();
        if (!term) return;
        saveRecentSearch(term);
        location.href = "/jobs?q=" + encodeURIComponent(term);
      });
    });
  }

  /* Notifications. The bell only appears once we know there is an account to
     load them for, so a signed-out visitor never sees a control that does
     nothing. Failures are silent: a nav that breaks because a nicety failed
     would be a worse trade than a missing badge. */

  /* Pageview tracking moved to the legacy nav when the Path & Pixel rebuild
     replaced it, and never came back: no page loads nav.js any more, so
     nothing had been logging a view since the cutover. The designer
     dashboard's "Profile views" was reading a table that had stopped
     growing — a permanently frozen number, which reads as nobody looking.

     Same shape as before and the same privacy line: the path only. No
     identifier, no cookie, nothing that ties two visits together. Query
     strings are stripped rather than trimmed, since those are where search
     terms and tokens end up. */
  function trackPageview(){
    try {
      var path = (location.pathname || "/").slice(0, 200);
      fetch("/api/track/pageview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path }),
        keepalive: true
      }).catch(function(){});
    } catch (e) {}
  }

  function wireBell(root){
    // Two headers render at different widths (desktop row and compact row), so
    // there are two bells. Both are wired against one shared item list — a
    // count that only updated on whichever header happened to be visible would
    // go stale the moment the window resized.
    var bells = Array.prototype.slice.call(root.querySelectorAll("[data-bell]"));
    var panels = Array.prototype.slice.call(root.querySelectorAll("[data-notif-panel]"));
    if (!bells.length || !panels.length) return;

    var token = null;
    try { token = localStorage.getItem(DESIGNER_TOKEN_KEY); } catch (e) {}
    if (!token) return;
    bells.forEach(function(b){ b.hidden = false; });

    var items = [];

    function paintCount(n){
      // The tab bar's dot is the same fact at a glance — no number, because a
      // count you can't read at 7px is just noise.
      root.querySelectorAll("[data-tab-unread]").forEach(function(d){ d.hidden = !n; });
      bells.forEach(function(b){
        var dot = b.querySelector("[data-bell-count]");
        if (!dot) return;
        dot.hidden = !n;
        dot.textContent = n > 9 ? "9+" : String(n);
      });
    }

    async function load(){
      try {
        var res = await fetch("/api/designers/me/notifications", {
          headers: { "Authorization": "Bearer " + token }
        });
        if (!res.ok) return;
        var data = await res.json();
        items = data.notifications || [];
        paintCount(data.unread || 0);
      } catch (e) {}
    }

    function paintPanel(){
      panels.forEach(function(panel){
      if (!items.length) {
        panel.innerHTML = '<p class="pp-notif-empty">Nothing yet. When a company messages you or saves your profile, it shows up here.</p>';
        return;
      }
      panel.innerHTML =
        '<div class="pp-notif-head"><span>Notifications</span>' +
          '<button type="button" data-notif-readall>Mark all read</button></div>' +
        items.map(function(n){
          return '<a class="pp-notif-item' + (n.read ? "" : " -unread") + '" href="' + (n.href || "#") + '">' +
            '<span class="t">' + n.title + '</span>' +
            (n.body ? '<span class="b">' + n.body + '</span>' : "") +
          '</a>';
        }).join("");
      var all = panel.querySelector("[data-notif-readall]");
      if (all) all.addEventListener("click", async function(e){
        e.stopPropagation();
        try {
          await fetch("/api/designers/me/notifications/read", {
            method: "POST", headers: { "Authorization": "Bearer " + token }
          });
        } catch (err) {}
        await load();
        paintPanel();
      });
      });
    }

    bells.forEach(function(bell, i){
      var panel = panels[i] || panels[0];
      bell.addEventListener("click", async function(e){
        e.stopPropagation();
        var opening = panel.hidden;
        panels.forEach(function(p){ p.hidden = true; });
        panel.hidden = !opening;
        if (opening) { await load(); paintPanel(); }
      });
      document.addEventListener("click", function(e){
        if (!panel.hidden && !panel.contains(e.target) && !bell.contains(e.target)) panel.hidden = true;
      });
    });

    load();
  }

  /* Four destinations, chosen because they are what a signed-in designer
     actually returns for. Signed-out visitors keep the hamburger: on the
     marketing pages the nav is a menu of places to look, not a set of tools,
     and a tab bar would be four taps to nowhere they have a reason to go. */
  var TAB_ITEMS = [
    { key: "jobs", label: "Roles", href: ROUTES.jobs,
      icon: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2"/>' },
    { key: "saved", label: "Saved", href: ROUTES.jobs + "?tab=saved",
      icon: '<path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/>' },
    { key: "messages", label: "Messages", href: "/dashboard",
      icon: '<path d="M21 15a2 2 0 01-2 2H8l-4 4V5a2 2 0 012-2h13a2 2 0 012 2z"/>' },
    { key: "profile", label: "Profile", href: "/dashboard",
      icon: '<circle cx="12" cy="8" r="3.6"/><path d="M4.5 20a7.5 7.5 0 0115 0"/>' }
  ];

  function tabBarHtml(user, active){
    if (!user) return "";
    var path = location.pathname;
    return '<nav class="pp-tabbar" aria-label="Main">' + TAB_ITEMS.map(function(t){
      // "active" here is the page's own declared section where it matches,
      // otherwise fall back to the path so the bar is never blank.
      var on = (t.key === "jobs" && (active === "jobs" || path === "/jobs")) ||
               (t.key === "profile" && path.indexOf("/dashboard") === 0);
      return '<a href="' + t.href + '"' + (on ? ' class="-active" aria-current="page"' : '') + '>' +
        svg(21, t.icon) + '<span class="pp-tab-label">' + t.label + '</span>' +
        (t.key === "messages" ? '<span class="pp-tab-dot" data-tab-unread hidden></span>' : "") +
      '</a>';
    }).join("") + '</nav>';
  }

  function authControlsHtml(user){
    if (user) {
      // The bell lives in the shared nav so it follows people around the site
      // rather than only existing on the dashboard they were trying to leave.
      return '<button type="button" class="pp-bell" data-bell aria-label="Notifications" hidden>' +
          ICONS.bell(19) + '<span class="pp-bell-dot" data-bell-count hidden></span></button>' +
        '<div class="pp-notif-panel" data-notif-panel hidden></div>' +
        '<div class="pp-avatar-menu" data-avatar-menu>' +
        '<button type="button" class="pp-avatar-chip" data-avatar-trigger aria-haspopup="true" aria-expanded="false">' +
          '<span class="pp-av-name">' + user.name + '</span>' +
          '<span class="pp-av-circle">' + avatarInnerHtml(user) + '</span></button>' +
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
            '<div class="pp-sresults" data-search-results hidden></div>' +
          '</div>' +
        '</div>' +
        '<div class="pp-header-compact">' +
          '<a class="pp-mark" href="' + ROUTES.home + '"><img src="/brand/pp-icon.png" alt="Path &amp; Pixel"></a>' +
          '<div class="pp-spacer"></div>' +
          '<button type="button" class="pp-tap-btn" data-open-msearch aria-label="Search">' + ICONS.search(19) + '</button>' +
          // Same bell on the compact header — a notification you can only see
          // on a wide screen is one most people never see.
          (user ? '<button type="button" class="pp-bell" data-bell aria-label="Notifications" hidden style="width:34px;height:34px">' +
              ICONS.bell(18) + '<span class="pp-bell-dot" data-bell-count hidden></span></button>' +
            '<div class="pp-notif-panel" data-notif-panel hidden></div>' : '') +
          (user ? '<a class="pp-av-circle" href="' + user.href + '" aria-label="Your profile" style="width:30px;height:30px;font-size:11px;text-decoration:none">' +
            avatarInnerHtml(user) + '</a>' : '') +
          '<button type="button" class="pp-tap-theme" data-theme-toggle aria-label="Toggle dark mode" title="Toggle dark mode">' + ICONS.theme(19) + '</button>' +
          '<button type="button" class="pp-tap-btn pp-menu-btn" data-open-menu aria-label="Menu">' + ICONS.hamburger(22) + '</button>' +
        '</div>' +
      '</header>' +
      tabBarHtml(user, active) +
      '<div class="pp-msearch" data-msearch>' +
        '<div class="pp-msearch-head">' +
          '<div class="pp-msearch-field" data-msearch-field>' + ICONS.search(17) +
            '<input type="text" data-msearch-input placeholder="Roles, designers, companies…">' +
            '<button type="button" class="pp-search-clear" data-clear-msearch hidden aria-label="Clear search">' + ICONS.clear(12) + '</button>' +
          '</div>' +
          '<button type="button" class="pp-msearch-cancel" data-close-msearch>Cancel</button>' +
        '</div>' +
        '<div class="pp-msearch-body">' +
          '<div class="pp-sresults" data-search-results hidden></div>' +
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
                '<span class="pp-av-circle">' + avatarInnerHtml(user) + '</span>' +
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

    // The full-screen menu is the same kind of overlay as the filter sheet —
    // it covers the page and locks scrolling — so it gets the same treatment
    // rather than a second, thinner version of it. Without this, Escape did
    // nothing, focus never left the hamburger, and a keyboard user was stuck
    // behind a locked page on every screen of the site.
    var menuSheet = PPSheet({
      panel: menu,
      toggle: root.querySelector("[data-open-menu]"),
      closeBtn: root.querySelector("[data-close-menu]")
    });

    root.querySelectorAll("[data-theme-toggle]").forEach(function(btn){
      btn.addEventListener("click", function(){
        var root2 = document.documentElement;
        var next = root2.getAttribute("data-pp-theme") === "dark" ? "light" : "dark";
        root2.setAttribute("data-pp-theme", next);
        try { localStorage.setItem("pp_theme", next); } catch (e) {}
      });
    });

    if (root.querySelector(".pp-tabbar")) document.body.classList.add("pp-has-tabbar");
    trackPageview();
    wireLiveSearch(root);
    wireBell(root);

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
