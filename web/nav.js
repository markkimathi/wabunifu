(function(){
  // Mobile hamburger menu, patterned after the sample/main.css + bundle.js
  // reference: a toggle button that morphs into an X, a full-screen
  // overlay (backdrop + sliding panel), and a staggered link reveal.
  // Kept dependency-free (plain CSS transitions, no GSAP) since most of
  // these pages don't otherwise load an animation library.
  function ready(fn){
    if(document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  // Analytics beacon: fire-and-forget POST, never lets a tracking failure
  // touch the page. sendBeacon survives the tab closing/navigating away,
  // which fetch() doesn't reliably do, so it's preferred where available.
  // See api/db.py's module docstring for exactly what this does and
  // doesn't store (no IPs, no cookies, no cross-visit identifiers).
  function track(endpoint, data){
    try{
      var body = JSON.stringify(data);
      if(navigator.sendBeacon){
        navigator.sendBeacon(endpoint, new Blob([body], {type: "application/json"}));
      } else {
        fetch(endpoint, {method: "POST", headers: {"Content-Type": "application/json"}, body: body, keepalive: true});
      }
    } catch(e){ /* analytics must never break the page */ }
  }
  window.kaziTrack = track;

  // Marks whichever nav link points at the current page with .active, so
  // the dot + purple styling in each page's <style> block picks it up.
  function markActiveNav(){
    var path = location.pathname.replace(/\/$/, "") || "/";
    document.querySelectorAll(".nav-links a, .mm-nav a").forEach(function(a){
      var href = a.getAttribute("href") || "";
      if(href.indexOf("mailto:") === 0) return;
      var linkPath = href.replace(/\/$/, "") || "/";
      a.classList.toggle("active", linkPath === path);
    });
  }

  // Page loader: every page starts with #pageLoader visible (see its
  // inline HTML, right after <body>) so there's no blank flash while
  // assets/fonts settle, then this hides it once the page is ready. It's
  // shown again right before an outgoing same-site navigation, so the
  // transition between pages reads as one continuous loading state
  // instead of an old page vanishing into blank white.
  function hidePageLoader(){
    var loader = document.getElementById("pageLoader");
    if(loader) loader.classList.add("-hide");
  }
  function showPageLoader(){
    var loader = document.getElementById("pageLoader");
    if(loader) loader.classList.remove("-hide");
  }
  document.addEventListener("click", function(e){
    var a = e.target.closest("a");
    if(!a) return;
    var href = a.getAttribute("href") || "";
    if(!href || href.indexOf("mailto:") === 0 || href.charAt(0) === "#") return;
    if(a.target === "_blank" || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    if(a.origin !== location.origin) return;
    showPageLoader();
  });
  // Back/forward restores from bfcache can land mid-"show" if the user
  // clicked away and then hit back before the next page loaded.
  window.addEventListener("pageshow", hidePageLoader);

  // Header account menu: swaps the "Sign in" link for an avatar+dropdown
  // when a designer session is present. Runs on every page (this file is
  // shared sitewide) so the logged-in state is consistent everywhere, not
  // just on /account. Silently does nothing on pages that don't have the
  // #acctMenu markup, and silently falls back to the logged-out "Sign in"
  // state if the stored token is missing or has expired.
  var ACCT_TOKEN_KEY = "kazi_designer_token";
  function getAcctToken(){
    return localStorage.getItem(ACCT_TOKEN_KEY) || sessionStorage.getItem(ACCT_TOKEN_KEY) || "";
  }
  function clearAcctToken(){
    localStorage.removeItem(ACCT_TOKEN_KEY);
    sessionStorage.removeItem(ACCT_TOKEN_KEY);
  }
  function initAccountMenu(){
    var menu = document.getElementById("acctMenu");
    var mmAccount = document.getElementById("mmAccount");
    var acctCta = document.getElementById("acctCta");
    var signinCta = document.getElementById("signinCta");
    var mmCta = document.getElementById("mmCta");
    var token = getAcctToken();
    if(!menu || !token) return;

    fetch("/api/designers/me", {headers: {"Authorization": "Bearer " + token}})
      .then(function(res){
        if(!res.ok) throw new Error("unauthorized");
        return res.json();
      })
      .then(function(me){
        document.getElementById("acctAvatar").src = me.photo_path || "/logo.png";
        document.querySelectorAll('a[href="/account"]').forEach(function(a){
          if(menu.contains(a) || (mmAccount && mmAccount.contains(a))) return;
          a.style.display = "none";
        });
        menu.classList.add("-visible");
        if(mmAccount) mmAccount.style.display = "";
        if(acctCta) acctCta.style.display = "none";
        if(signinCta) signinCta.style.display = "none";
        if(mmCta) mmCta.style.display = "none";

        var trigger = document.getElementById("acctTrigger");
        trigger.addEventListener("click", function(e){
          e.stopPropagation();
          var open = menu.classList.toggle("-open");
          trigger.setAttribute("aria-expanded", open ? "true" : "false");
        });
        document.addEventListener("click", function(e){
          if(!menu.contains(e.target)) menu.classList.remove("-open");
        });
        window.addEventListener("keyup", function(e){
          if(e.key === "Escape") menu.classList.remove("-open");
        });

        function doLogout(){
          fetch("/api/designers/logout", {method: "POST", headers: {"Authorization": "Bearer " + token}})
            .catch(function(){});
          clearAcctToken();
          location.href = "/";
        }
        document.getElementById("acctLogoutBtn").addEventListener("click", doLogout);
        var mmLogoutBtn = document.getElementById("mmLogoutBtn");
        if(mmLogoutBtn) mmLogoutBtn.addEventListener("click", doLogout);
      })
      .catch(function(){
        clearAcctToken();
      });
  }

  // Live role count pill in the header (".bar-stat"): shared across every
  // page, not just the jobs listing, so it's driven by the lightweight
  // /api/jobs/count endpoint here rather than each page fetching the full
  // jobs payload just to read its length.
  function initLiveCount(){
    var stat = document.querySelector(".bar-stat");
    if(!stat) return;
    fetch("/api/jobs/count", {cache: "no-store"})
      .then(function(res){ return res.ok ? res.json() : null; })
      .then(function(data){
        if(!data) return;
        var d = new Date(data.generated_at);
        stat.innerHTML =
          '<span class="livedot"></span><b id="liveCount">' + data.count +
          '</b><span class="stattext"> roles · updated ' +
          d.toLocaleDateString('en-GB', {day: 'numeric', month: 'short'}) + '</span>';
      })
      .catch(function(){});
  }

  ready(function(){
    hidePageLoader();
    markActiveNav();
    track("/api/track/pageview", {path: location.pathname});
    initAccountMenu();
    initLiveCount();
    var toggle = document.querySelector(".menu-toggle");
    var menu = document.querySelector(".mobile-menu");
    if(!toggle || !menu) return;
    var backdrop = menu.querySelector(".mm-backdrop");
    var closeBtn = menu.querySelector(".mm-close");

    function open(){
      menu.classList.add("-open");
      toggle.classList.add("-active");
      toggle.setAttribute("aria-expanded", "true");
      document.documentElement.classList.add("menu-open");
    }
    function close(){
      menu.classList.remove("-open");
      toggle.classList.remove("-active");
      toggle.setAttribute("aria-expanded", "false");
      document.documentElement.classList.remove("menu-open");
    }

    toggle.addEventListener("click", function(){
      menu.classList.contains("-open") ? close() : open();
    });
    if(backdrop) backdrop.addEventListener("click", close);
    if(closeBtn) closeBtn.addEventListener("click", close);
    menu.querySelectorAll("a").forEach(function(a){
      a.addEventListener("click", close);
    });
    window.addEventListener("keyup", function(e){
      if(e.key === "Escape") close();
    });
  });
})();
