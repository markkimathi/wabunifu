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

  // A plain colored disc, fixed to the viewport, that shrinks away from
  // the toggle button's own position (getBoundingClientRect), so it's
  // correct regardless of scroll position.
  function wipe(next, x, y){
    var reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if(reduceMotion || typeof Element === "undefined" || !Element.prototype.animate){
      applyTheme(next);
      return;
    }

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
