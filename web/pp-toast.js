(function(){
  // Path & Pixel toast: a single sticky-bottom notice, same visual language
  // as the mockups' post-action confirmations (dark pill, gold checkmark,
  // centered). Self-injecting like pp-confirm.js — window.showPPToast, not
  // a return value to await, since a toast doesn't block on a decision.
  //
  // Usage:
  //   showPPToast("Link copied");
  //   showPPToast("Here's how reporting works: ...", { duration: 5000 });
  if (window.showPPToast) return;

  function ready(fn){
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  var STYLE = ""
    + ".pp-ts-wrap{position:fixed;left:0;right:0;bottom:24px;z-index:var(--pp-z-toast,30);"
    + "display:flex;justify-content:center;pointer-events:none;padding:0 20px}"
    + ".pp-ts-pill{display:flex;align-items:center;gap:12px;padding:14px 18px;border-radius:var(--pp-radius-lg);"
    + "background:var(--pp-ink);box-shadow:0 12px 32px rgba(10,10,10,.28);max-width:440px;"
    + "opacity:0;transform:translateY(8px);transition:opacity var(--pp-duration) var(--pp-ease),transform var(--pp-duration) var(--pp-ease)}"
    + ".pp-ts-pill.-open{opacity:1;transform:translateY(0)}"
    + ".pp-ts-pill svg{flex:none;color:var(--pp-gold)}"
    + ".pp-ts-text{font-size:14px;line-height:1.5;color:var(--pp-white);font-family:var(--pp-font)}";

  ready(function(){
    var styleEl = document.createElement("style");
    styleEl.textContent = STYLE;
    document.head.appendChild(styleEl);

    var wrap = document.createElement("div");
    wrap.className = "pp-ts-wrap";
    wrap.innerHTML =
      '<div class="pp-ts-pill" role="status" aria-live="polite">' +
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5.5 5.5L20 7"></path></svg>' +
        '<span class="pp-ts-text"></span>' +
      '</div>';
    document.body.appendChild(wrap);

    var pill = wrap.querySelector(".pp-ts-pill");
    var textEl = wrap.querySelector(".pp-ts-text");
    var hideTimer = null;

    window.showPPToast = function(message, opts){
      opts = opts || {};
      textEl.textContent = message || "";
      pill.classList.add("-open");
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(function(){
        pill.classList.remove("-open");
      }, opts.duration || 3200);
    };
  });
})();
