(function(){
  // Path & Pixel destructive confirm: a centred dialog on desktop/tablet,
  // a bottom sheet on mobile (<768px, matching tokens.css's own tablet-
  // portrait cutoff). Same self-injecting pattern as this codebase's
  // existing confirm-dialog.js, restyled to the P&P spec: no icon, no
  // header band, no close ×, larger title, right-aligned buttons on
  // desktop, stacked full-width buttons with the destructive one on top
  // on mobile. Deliberately a separate component from confirm-dialog.js
  // (window.showPPConfirm, not showConfirmDialog) rather than a restyle
  // of it in place, since the two are not wired into the same pages yet
  // — see README's "Working on this with Claude Code" section.
  //
  // Usage:
  //   var ok = await showPPConfirm({
  //     title: "Close your account?",
  //     description: "Your profile comes down immediately and your saved roles are deleted.",
  //     confirmLabel: "Close account", cancelLabel: "Keep my account", danger: true,
  //   });
  //   if (!ok) return;
  //
  // danger:true = account loss/deletion, red fill. danger:false (default)
  // = merely disruptive but recoverable (e.g. sign out), dark fill. Gold
  // never carries a destructive action, per house rules.
  // Guards against the script being included twice on one page, which built a
  // second dialog and a second window.showPPConfirm, each owning its own
  // backdrop: the function callers reached showed one element while the other
  // stayed hidden, so the dialog never appeared and its promise never
  // resolved. Sign out, delete account and delete project all hung on that.
  //
  // The flag is set synchronously, at script-evaluation time. Testing for
  // showPPConfirm — as this did — never worked, because that is assigned
  // inside the DOMContentLoaded callback below and both copies evaluate long
  // before it fires.
  if (window.__ppConfirmLoaded) return;
  window.__ppConfirmLoaded = true;

  function ready(fn){
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  var STYLE = ""
    + ".pp-cf-backdrop{position:fixed;inset:0;z-index:var(--pp-z-modal,20);background:rgba(10,10,10,.45);"
    + "display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box;"
    + "opacity:0;pointer-events:none;transition:opacity var(--pp-duration) var(--pp-ease)}"
    + ".pp-cf-backdrop.-open{opacity:1;pointer-events:auto}"
    + ".pp-cf-panel{background:var(--c-surface);border-radius:var(--pp-radius-lg);"
    + "box-shadow:0 20px 56px rgba(10,10,10,.22);max-width:460px;width:100%;padding:28px;box-sizing:border-box;"
    + "transform:scale(.96) translateY(6px);opacity:0;"
    + "transition:transform var(--pp-duration) var(--pp-ease),opacity var(--pp-duration) var(--pp-ease)}"
    + ".pp-cf-backdrop.-open .pp-cf-panel{transform:scale(1) translateY(0);opacity:1}"
    + ".pp-cf-handle{display:none}"
    + ".pp-cf-title{font-size:23px;font-weight:800;letter-spacing:-0.022em;line-height:1.15;"
    + "margin:0 0 8px;color:var(--c-text);font-family:inherit}"
    + ".pp-cf-desc{font-size:15px;line-height:1.6;color:var(--c-text-subtle);margin:0 0 20px;font-family:inherit}"
    + ".pp-cf-desc:empty{display:none;margin:0 0 4px}"
    + ".pp-cf-actions{display:flex;gap:10px;justify-content:flex-end}"
    + ".pp-cf-btn{font:inherit;font-size:14.5px;font-weight:600;height:44px;padding:0 16px;border-radius:var(--pp-radius-lg);"
    + "cursor:pointer;font-family:var(--pp-font);transition:background var(--pp-duration) var(--pp-ease),border-color var(--pp-duration) var(--pp-ease)}"
    + ".pp-cf-btn-safe{background:var(--c-surface);border:1px solid var(--c-border-strong);color:var(--c-text-muted)}"
    + ".pp-cf-btn-safe:hover{background:var(--c-bg-sunken);border-color:var(--c-border-hover)}"
    // Self-inverting (--c-text/--c-bg), not a literal --pp-ink fill —
    // see pp-primitives.css's .pp-btn-dark comment for why: --pp-ink
    // equals the dark-theme page background and would make this vanish.
    + ".pp-cf-btn-confirm{background:var(--c-text);border:1px solid var(--c-text);color:var(--c-bg)}"
    + ".pp-cf-btn-confirm:hover{filter:brightness(.85)}"
    + ".pp-cf-btn-confirm.-danger{background:var(--c-bad);border-color:var(--c-bad)}"
    + ".pp-cf-btn-confirm.-danger:hover{background:var(--c-bad-text)}"
    + ".pp-cf-btn:disabled{opacity:.6;cursor:not-allowed}"
    + "@media(max-width:767px){"
    + ".pp-cf-backdrop{align-items:flex-end;padding:0}"
    + ".pp-cf-panel{max-width:none;border-radius:var(--pp-radius-2xl,16px) var(--pp-radius-2xl,16px) 0 0;"
    + "padding:8px 20px 28px;transform:translateY(100%)}"
    + ".pp-cf-backdrop.-open .pp-cf-panel{transform:translateY(0)}"
    + ".pp-cf-handle{display:block;width:38px;height:4px;border-radius:var(--pp-radius-full);"
    + "background:var(--c-border-strong);margin:0 auto 18px}"
    + ".pp-cf-title{font-size:20px}"
    + ".pp-cf-actions{flex-direction:column;gap:8px}"
    + ".pp-cf-btn{width:100%;height:50px}"
    + ".pp-cf-btn-safe{order:2;background:transparent;border:none}"
    + ".pp-cf-btn-safe:hover{background:var(--c-surface-inset)}"
    + ".pp-cf-btn-confirm{order:1}"
    + "}";

  ready(function(){
    var styleEl = document.createElement("style");
    styleEl.textContent = STYLE;
    document.head.appendChild(styleEl);

    var backdrop = document.createElement("div");
    backdrop.className = "pp-cf-backdrop";
    backdrop.innerHTML =
      '<div class="pp-cf-panel" role="alertdialog" aria-modal="true" aria-labelledby="ppCfTitle" aria-describedby="ppCfDesc">' +
        '<div class="pp-cf-handle"></div>' +
        '<p class="pp-cf-title" id="ppCfTitle"></p>' +
        '<p class="pp-cf-desc" id="ppCfDesc"></p>' +
        '<div class="pp-cf-actions">' +
          '<button type="button" class="pp-cf-btn pp-cf-btn-safe" id="ppCfSafeBtn"></button>' +
          '<button type="button" class="pp-cf-btn pp-cf-btn-confirm" id="ppCfConfirmBtn"></button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(backdrop);

    var titleEl = document.getElementById("ppCfTitle");
    var descEl = document.getElementById("ppCfDesc");
    var safeBtn = document.getElementById("ppCfSafeBtn");
    var confirmBtn = document.getElementById("ppCfConfirmBtn");

    var activeResolve = null;
    var lastFocused = null;

    function close(result){
      if (!backdrop.classList.contains("-open")) return;
      backdrop.classList.remove("-open");
      document.removeEventListener("keydown", onKeydown);
      if (lastFocused && lastFocused.focus) lastFocused.focus();
      if (activeResolve) {
        var resolve = activeResolve;
        activeResolve = null;
        resolve(result);
      }
    }
    function onKeydown(e){
      if (e.key === "Escape") close(false);
    }
    backdrop.addEventListener("mousedown", function(e){
      if (e.target === backdrop) close(false);
    });
    safeBtn.addEventListener("click", function(){ close(false); });
    confirmBtn.addEventListener("click", function(){ close(true); });

    // Both buttons must name their outcome — never "OK"/"Cancel". Callers
    // are expected to pass real labels (e.g. confirmLabel:"Sign out",
    // safeLabel:"Stay signed in"); the fallbacks below exist only so a
    // missing label fails visibly rather than throwing.
    window.showPPConfirm = function(opts){
      opts = opts || {};
      lastFocused = document.activeElement;
      titleEl.textContent = opts.title || "";
      descEl.textContent = opts.description || "";
      confirmBtn.textContent = opts.confirmLabel || "Continue";
      confirmBtn.classList.toggle("-danger", !!opts.danger);
      safeBtn.textContent = opts.safeLabel || opts.cancelLabel || "Cancel";
      return new Promise(function(resolve){
        activeResolve = resolve;
        document.addEventListener("keydown", onKeydown);
        // The rAF is what lets the open transition run — the class has to land
        // on a later frame than the display change. But rAF does not fire at
        // all while the tab is hidden, and a dialog that never appears also
        // never resolves its promise, so the caller waits forever with nothing
        // on screen. The timeout is the floor: whichever runs first wins.
        var revealed = false;
        function reveal(){
          if (revealed) return;
          revealed = true;
          backdrop.classList.add("-open");
          confirmBtn.focus();
        }
        requestAnimationFrame(reveal);
        setTimeout(reveal, 60);
      });
    };
  });
})();
