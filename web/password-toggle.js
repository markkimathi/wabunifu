(function(){
  // Reusable show/hide toggle for every password field: self-contained,
  // same pattern as confirm-dialog.js — one <script> tag, no per-field
  // markup or wiring on any page. On DOMContentLoaded it finds every
  // input[type="password"] already on the page, wraps it, and adds an
  // eye-icon button that flips the input between "password" and "text".
  // Toggling swaps the `type` attribute only — the value is never
  // touched, and selection is explicitly saved/restored around the
  // swap so the cursor position survives it.
  if (window.enhancePasswordFields) return; // don't double-init if this ever loads twice

  function ready(fn){
    if(document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  var ICON_EYE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/></svg>';
  var ICON_EYE_OFF = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/></svg>';

  var STYLE = ""
    + ".pw-field-wrap{position:relative;display:block;width:100%}"
    + ".pw-field-wrap input{width:100%;padding-right:40px}"
    // Every selector below is scoped through both wrapper classes
    // (.pw-field-wrap .pw-toggle-btn) rather than just ".pw-toggle-btn" —
    // this component gets dropped onto pages that already have generic,
    // higher-specificity rules like ".gate button{width:100%}" targeting
    // any <button> in their forms, which would otherwise stretch/style
    // this button too since a plain single-class selector loses to a
    // class+tag one regardless of source order.
    // !important on the box-model/background properties because host
    // pages define their own generic ":hover"/":active" rules (e.g.
    // ".gate button:hover{background:var(--brand)}") whose specificity
    // can beat a plain non-!important override on *this* state even
    // though the base (non-hover) rule above already won — each pseudo-
    // class state is matched and cascaded independently.
    + ".pw-field-wrap .pw-toggle-btn{position:absolute!important;top:50%!important;right:4px!important;"
    + "left:auto!important;bottom:auto!important;transform:translateY(-50%)!important;"
    + "width:auto!important;height:auto!important;min-width:0!important;max-width:none!important;"
    + "background:none!important;border:0!important;padding:8px!important;margin:0!important;"
    + "line-height:0;cursor:pointer;color:var(--faint,#828282);display:flex;align-items:center;"
    + "justify-content:center;border-radius:8px;transition:color .2s var(--ease-expo,ease)}"
    + ".pw-field-wrap .pw-toggle-btn:hover{background:none!important;color:var(--brand,#6D28A1)}"
    + ".pw-field-wrap .pw-toggle-btn:focus-visible{outline:2px solid var(--brand,#6D28A1);outline-offset:1px}";

  // Firefox has historically thrown on selectionStart/selectionEnd reads
  // for type="password" inputs (the exact type we're reading from when
  // revealing) — fall back to "cursor at end" there instead of crashing.
  function getSelection(input){
    try { return [input.selectionStart, input.selectionEnd]; }
    catch (e) { return [input.value.length, input.value.length]; }
  }
  function setSelection(input, start, end){
    try { input.setSelectionRange(start, end); } catch (e) { /* not all types support it */ }
  }

  function enhance(input){
    if (input.dataset.pwEnhanced) return;
    input.dataset.pwEnhanced = "1";

    var wrap = document.createElement("div");
    wrap.className = "pw-field-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var btn = document.createElement("button");
    btn.type = "button"; // never submit the surrounding form
    btn.className = "pw-toggle-btn";
    btn.setAttribute("aria-label", "Show password");
    btn.setAttribute("aria-pressed", "false");
    btn.innerHTML = ICON_EYE;
    wrap.appendChild(btn);

    btn.addEventListener("click", function(){
      var hidden = input.type === "password";
      var sel = getSelection(input);
      input.type = hidden ? "text" : "password";
      btn.innerHTML = hidden ? ICON_EYE_OFF : ICON_EYE;
      btn.setAttribute("aria-label", hidden ? "Hide password" : "Show password");
      btn.setAttribute("aria-pressed", hidden ? "true" : "false");
      input.focus();
      setSelection(input, sel[0], sel[1]);
    });
  }

  function enhancePasswordFields(root){
    (root || document).querySelectorAll('input[type="password"]').forEach(enhance);
  }
  window.enhancePasswordFields = enhancePasswordFields;

  ready(function(){
    var styleEl = document.createElement("style");
    styleEl.textContent = STYLE;
    document.head.appendChild(styleEl);
    enhancePasswordFields();
  });
})();
