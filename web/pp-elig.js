/* Eligibility badges — the one place the site decides what an eligibility
   verdict *says*.
   ---------------------------------------------------------------------------
   Five pages each carried their own copy of this map, which is how they drifted
   into all showing "Open across Africa" for a role scoped to "Remote, Nigeria".
   The scraper now sends a scope alongside the badge (see
   scraper/pipeline/eligibility.py, which this mirrors), so a verdict is a pair:

     elig  = kenya | africa | world | check
     scope = "" when the badge stands on its own (genuinely continent-wide, or
             genuinely nothing stated), otherwise the country, countries or
             sub-region the role is actually limited to — including where that
             is somewhere outside Africa entirely ("the United States").

   Load before the page script:  <script src="pp-elig.js"></script>
*/
(function (global) {
  "use strict";

  // Sub-regions expanded to member countries, so "open to me?" can be answered
  // for a posting scoped to a region rather than a country. Mirrors
  // REGION_MEMBERS in the scraper.
  var REGION_MEMBERS = {
    "East Africa": ["Kenya", "Tanzania", "Uganda", "Rwanda", "Burundi", "Ethiopia",
                    "Somalia", "South Sudan", "Djibouti", "Eritrea"],
    "West Africa": ["Nigeria", "Ghana", "Senegal", "Ivory Coast", "Mali", "Burkina Faso",
                    "Benin", "Togo", "Guinea", "Sierra Leone", "Liberia", "Niger",
                    "Gambia", "Guinea-Bissau", "Cabo Verde", "Mauritania"],
    "North Africa": ["Egypt", "Morocco", "Algeria", "Tunisia", "Libya", "Sudan"],
    "Southern Africa": ["South Africa", "Namibia", "Botswana", "Zimbabwe", "Zambia",
                        "Mozambique", "Lesotho", "Eswatini", "Malawi", "Angola"],
    "Central Africa": ["Cameroon", "Chad", "Central African Republic", "Gabon",
                       "DR Congo", "Congo", "Equatorial Guinea", "Sao Tome and Principe"]
  };

  function scopeList(scope) {
    var out = [];
    (scope || "").split(",").forEach(function (part) {
      var p = part.trim();
      if (!p) return;
      out = out.concat(REGION_MEMBERS[p] || [p]);
    });
    return out;
  }

  /* true / false / null — null meaning we genuinely don't know, which callers
     must render as "worth checking" rather than as a verdict either way. */
  function openTo(elig, scope, country) {
    if (!country) return null;
    if (scope) return scopeList(scope).indexOf(country) !== -1;
    if (elig === "world") return true;
    if (elig === "kenya" || elig === "africa") return true;
    return null;
  }

  /* {text, tone} where tone is "open" | "neutral".
     Without a viewer country the honest reading of a scoped role is factual,
     not a verdict: state where it is open and let the reader place themselves.
     Pass `country` and it becomes personal. */
  function label(elig, scope, country) {
    var verdict = country ? openTo(elig, scope, country) : null;

    if (country && verdict === true) {
      return { text: "Open to you", tone: "open" };
    }
    if (country && verdict === false) {
      return { text: scope ? "Only in " + scope : "Not open to you", tone: "neutral" };
    }
    // Impersonal, or genuinely unknown.
    if (elig === "world") return { text: "Global remote", tone: "open" };
    if (!scope) {
      if (elig === "africa" || elig === "kenya") return { text: "Open across Africa", tone: "open" };
      return { text: "Worth checking", tone: "neutral" };
    }
    return { text: "Open in " + scope, tone: "neutral" };
  }

  /* The sentence under the badge on a job page — why we say what we say.
     Never claims more certainty than the posting gave us. */
  function note(elig, scope, country) {
    var verdict = country ? openTo(elig, scope, country) : null;
    if (elig === "world" && !scope) {
      return "This role is open to remote candidates without a location restriction.";
    }
    if (!scope) {
      if (elig === "africa" || elig === "kenya") {
        return "This company's listing states where it can hire, and it covers countries across Africa.";
      }
      return "The posting doesn't say where you can work from. We've flagged it as worth checking — " +
             "ask before you invest time applying.";
    }
    if (verdict === true) {
      return "This posting is scoped to " + scope + ", which includes " + country + ".";
    }
    if (verdict === false) {
      return "This posting is scoped to " + scope + ", which doesn't include " + country + ".";
    }
    return "This posting is scoped to " + scope + ". If you're not based there, ask before you " +
           "invest time applying.";
  }

  function badge(elig, scope, country) {
    var m = label(elig, scope, country);
    if (m.tone === "open") {
      return '<span class="pp-elig -positive"><span class="pp-elig-dot"></span>' + m.text + "</span>";
    }
    return '<span class="pp-elig -check">' + m.text + "</span>";
  }

  /* The signed-in designer's country, or "" — set once by the page from
     /api/designers/me so badges and filtering agree on who is asking. */
  var viewerCountry = "";
  function setViewerCountry(c) { viewerCountry = c || ""; }
  function getViewerCountry() { return viewerCountry; }

  global.PPElig = {
    REGION_MEMBERS: REGION_MEMBERS,
    openTo: openTo,
    label: label,
    note: note,
    badge: badge,
    setViewerCountry: setViewerCountry,
    getViewerCountry: getViewerCountry
  };
})(window);
