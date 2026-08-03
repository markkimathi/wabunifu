(function(){
  // Reusable confirmation dialog: a modal on desktop/tablet, a bottom sheet
  // on mobile (the same breakpoint the rest of the site switches at,
  // max-width:600px). Self-contained on purpose — injects its own <style>
  // and DOM once per page load — so any page just needs this one
  // <script> tag, no per-page markup/CSS to keep in sync. Reads the
  // page's existing CSS custom properties (--brand, --surface, etc.,
  // already defined on :root by every page) so it matches the design
  // system without redefining colors of its own.
  //
  // Usage:
  //   var ok = await showConfirmDialog({
  //     title: "Delete your account?",
  //     description: "This can't be undone.",
  //     confirmLabel: "Delete", cancelLabel: "Cancel", danger: true,
  //   });
  //   if (!ok) return;
  //
  //   await showAlertDialog({title: "Something went wrong", description: err.message});
  if (window.showConfirmDialog) return; // don't double-init if this ever loads twice

  // This script tag sits in <head> (alongside theme.js/nav.js) on every
  // page, so it runs before <body> exists — defer the DOM injection to
  // DOMContentLoaded (same pattern as nav.js's own ready() helper).
  function ready(fn){
    if(document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  var STYLE = ""
    + ".cd-backdrop{position:fixed;inset:0;z-index:900;background:rgba(0,0,0,.5);"
    + "display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box;"
    + "opacity:0;pointer-events:none;transition:opacity .25s var(--ease-expo,ease)}"
    + ".cd-backdrop.-open{opacity:1;pointer-events:auto}"
    + ".cd-panel{background:var(--surface,#fff);border-radius:var(--radius,16px);"
    + "box-shadow:var(--shadow,0 8px 24px rgba(0,0,0,.18));max-width:400px;width:100%;"
    + "padding:28px;box-sizing:border-box;text-align:center;"
    + "transform:scale(.94) translateY(8px);opacity:0;"
    + "transition:transform .25s var(--ease-expo,ease),opacity .25s var(--ease-expo,ease)}"
    + ".cd-backdrop.-open .cd-panel{transform:scale(1) translateY(0);opacity:1}"
    + ".cd-handle{display:none}"
    + ".cd-icon{width:52px;height:52px;border-radius:50%;display:grid;place-items:center;margin:0 auto 16px;"
    + "background:var(--brand-soft,#eee);color:var(--brand,#6D28A1)}"
    + ".cd-icon:empty{display:none}"
    + ".cd-icon.-danger{background:var(--red-soft,#fbe4e2);color:var(--red,#D3322A)}"
    + ".cd-icon svg{display:block}"
    + ".cd-title{font-size:17px;font-weight:800;margin:0 0 8px;color:var(--ink,#0B1623);"
    + "font-family:inherit}"
    + ".cd-desc{font-size:14px;color:var(--muted,#828282);margin:0 0 22px;line-height:1.5;"
    + "font-family:inherit}"
    + ".cd-desc:empty{display:none;margin:0 0 6px}"
    + ".cd-actions{display:flex;gap:10px;justify-content:center}"
    + ".cd-btn{flex:1;font:inherit;font-size:14.5px;font-weight:700;padding:13px 18px;border-radius:999px;"
    + "cursor:pointer;transition:background .25s var(--ease-expo,ease),border-color .25s var(--ease-expo,ease),"
    + "opacity .15s ease}"
    + ".cd-btn-cancel{background:none;border:1px solid var(--hairline-strong,#ccc);color:var(--ink,#0B1623)}"
    + ".cd-btn-cancel:hover{border-color:var(--brand,#6D28A1)}"
    + ".cd-btn-confirm{background:var(--brand,#6D28A1);border:1px solid transparent;color:#fff}"
    + ".cd-btn-confirm:hover{background:var(--brand-light,#AA52ED)}"
    + ".cd-btn-confirm.-danger{background:var(--red,#D3322A)}"
    + ".cd-btn-confirm.-danger:hover{filter:brightness(1.1)}"
    + ".cd-btn:disabled{opacity:.6;cursor:not-allowed}"
    + "@media(max-width:600px){"
    + ".cd-backdrop{align-items:flex-end;padding:0}"
    + ".cd-panel{max-width:none;border-radius:20px 20px 0 0;padding:14px 20px 28px;"
    + "transform:translateY(100%);text-align:left}"
    + ".cd-backdrop.-open .cd-panel{transform:translateY(0)}"
    + ".cd-handle{display:block;width:36px;height:4px;border-radius:999px;"
    + "background:var(--hairline-strong,#ccc);margin:0 auto 18px}"
    + ".cd-icon{margin:0 0 14px}"
    + ".cd-actions{flex-direction:column-reverse}"
    + "}";

  ready(function(){
    var styleEl = document.createElement("style");
    styleEl.textContent = STYLE;
    document.head.appendChild(styleEl);

    var backdrop = document.createElement("div");
    backdrop.className = "cd-backdrop";
    backdrop.innerHTML =
      '<div class="cd-panel" role="alertdialog" aria-modal="true" aria-labelledby="cdTitle" aria-describedby="cdDesc">' +
        '<div class="cd-handle"></div>' +
        '<div class="cd-icon" id="cdIcon"></div>' +
        '<p class="cd-title" id="cdTitle"></p>' +
        '<p class="cd-desc" id="cdDesc"></p>' +
        '<div class="cd-actions">' +
          '<button type="button" class="cd-btn cd-btn-cancel" id="cdCancelBtn">Cancel</button>' +
          '<button type="button" class="cd-btn cd-btn-confirm" id="cdConfirmBtn">Confirm</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(backdrop);

    var iconEl = document.getElementById("cdIcon");
    var titleEl = document.getElementById("cdTitle");
    var descEl = document.getElementById("cdDesc");
    var cancelBtn = document.getElementById("cdCancelBtn");
    var confirmBtn = document.getElementById("cdConfirmBtn");

    var activeResolve = null;
    var lastFocused = null;

    function close(result){
      if(!backdrop.classList.contains("-open")) return;
      backdrop.classList.remove("-open");
      document.removeEventListener("keydown", onKeydown);
      if(lastFocused && lastFocused.focus) lastFocused.focus();
      if(activeResolve){
        var resolve = activeResolve;
        activeResolve = null;
        resolve(result);
      }
    }
    function onKeydown(e){
      if(e.key === "Escape") close(false);
    }
    backdrop.addEventListener("mousedown", function(e){
      if(e.target === backdrop) close(false);
    });
    cancelBtn.addEventListener("click", function(){ close(false); });
    confirmBtn.addEventListener("click", function(){ close(true); });

    // title/description/confirmLabel/cancelLabel/icon/danger are all
    // configurable per call; cancelLabel:null hides the Cancel button
    // entirely (used for plain "OK"-only alerts via showAlertDialog below).
    window.showConfirmDialog = function(opts){
      opts = opts || {};
      lastFocused = document.activeElement;
      iconEl.innerHTML = opts.icon || "";
      iconEl.classList.toggle("-danger", !!opts.danger);
      titleEl.textContent = opts.title || "";
      descEl.textContent = opts.description || "";
      confirmBtn.textContent = opts.confirmLabel || "Confirm";
      confirmBtn.classList.toggle("-danger", !!opts.danger);
      if(opts.cancelLabel === null){
        cancelBtn.style.display = "none";
      } else {
        cancelBtn.style.display = "";
        cancelBtn.textContent = opts.cancelLabel || "Cancel";
      }
      return new Promise(function(resolve){
        activeResolve = resolve;
        document.addEventListener("keydown", onKeydown);
        requestAnimationFrame(function(){
          backdrop.classList.add("-open");
          confirmBtn.focus();
        });
      });
    };

    // Same component, single "OK" button — for replacing alert().
    window.showAlertDialog = function(opts){
      opts = opts || {};
      var merged = {};
      for(var k in opts) if(opts.hasOwnProperty(k)) merged[k] = opts[k];
      merged.cancelLabel = null;
      merged.confirmLabel = opts.confirmLabel || "OK";
      return window.showConfirmDialog(merged);
    };
  });
})();
