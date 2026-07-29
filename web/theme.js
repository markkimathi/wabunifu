(function(){
  var KEY = "kazi_theme";
  var root = document.documentElement;

  function preferred(){
    var saved = localStorage.getItem(KEY);
    if(saved === "light" || saved === "dark") return saved;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  root.setAttribute("data-theme", preferred());

  function syncToggles(theme){
    document.querySelectorAll(".theme-toggle").forEach(function(btn){
      btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    });
  }

  function applyTheme(theme){
    root.setAttribute("data-theme", theme);
    localStorage.setItem(KEY, theme);
    syncToggles(theme);
  }

  // Lets the default UA cross-fade get out of the way so only our own
  // clip-path reveal plays, and makes sure the new snapshot draws on
  // top of the old one so its growing circle is what's visible.
  var vtStyle = document.createElement("style");
  vtStyle.textContent =
    "::view-transition-old(root),::view-transition-new(root){animation:none;mix-blend-mode:normal}" +
    "::view-transition-old(root){z-index:1}::view-transition-new(root){z-index:2}";
  document.head.appendChild(vtStyle);

  // Real "content wipes in" reveal: the View Transitions API snapshots
  // the actual before/after page (not just a flat color) so growing a
  // clip-path circle on the new snapshot shows real content sweeping
  // in from the toggle, with the old page staying visible underneath
  // until the circle covers it.
  function viewTransitionWipe(next, x, y){
    var maxR = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    );
    var transition = document.startViewTransition(function(){ applyTheme(next); });
    transition.ready.then(function(){
      root.animate(
        { clipPath: [
          "circle(0px at " + x + "px " + y + "px)",
          "circle(" + maxR + "px at " + x + "px " + y + "px)"
        ] },
        { duration: 900, easing: "cubic-bezier(.65,0,.35,1)", pseudoElement: "::view-transition-new(root)" }
      );
    }).catch(function(){});
  }

  // Fallback for browsers without View Transitions: a plain colored
  // disc that shrinks away, shown only until it can be replaced.
  function fallbackWipe(next, x, y){
    var oldCanvas = getComputedStyle(root).getPropertyValue("--canvas").trim();

    var maxR = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    );
    var size = maxR * 2;

    var clip = document.createElement("div");
    clip.style.position = "fixed";
    clip.style.inset = "0";
    clip.style.zIndex = "99999";
    clip.style.pointerEvents = "none";
    clip.style.overflow = "hidden";

    var circle = document.createElement("div");
    circle.style.position = "absolute";
    circle.style.left = (x - maxR) + "px";
    circle.style.top = (y - maxR) + "px";
    circle.style.width = size + "px";
    circle.style.height = size + "px";
    circle.style.borderRadius = "50%";
    circle.style.background = oldCanvas;
    circle.style.willChange = "transform";
    circle.style.transform = "scale(1)";

    clip.appendChild(circle);
    document.body.appendChild(clip);

    applyTheme(next);

    var anim = circle.animate(
      [{ transform: "scale(1)" }, { transform: "scale(0)" }],
      { duration: 900, easing: "cubic-bezier(.65,0,.35,1)", fill: "forwards" }
    );
    anim.onfinish = function(){ clip.remove(); };
    anim.oncancel = function(){ clip.remove(); };
  }

  function wipe(next, x, y){
    var reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if(reduceMotion){
      applyTheme(next);
    } else if(typeof document.startViewTransition === "function"){
      viewTransitionWipe(next, x, y);
    } else if(typeof Element !== "undefined" && Element.prototype.animate){
      fallbackWipe(next, x, y);
    } else {
      applyTheme(next);
    }
  }

  window.kaziToggleTheme = function(evt){
    var btn = (evt && evt.currentTarget) || document.querySelector(".theme-toggle");
    var x, y;
    if(btn && btn.getBoundingClientRect){
      var r = btn.getBoundingClientRect();
      x = r.left + r.width / 2; y = r.top + r.height / 2;
    } else {
      x = window.innerWidth - 40; y = 34;
    }
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    wipe(next, x, y);
  };

  document.addEventListener("DOMContentLoaded", function(){
    syncToggles(root.getAttribute("data-theme"));
    document.querySelectorAll(".theme-toggle").forEach(function(btn){
      btn.addEventListener("click", window.kaziToggleTheme);
    });
  });
})();
