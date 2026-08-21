(function(){
  // Reusable Featured Projects component: one data model, one card
  // renderer, one add/edit form, one manage-grid controller — shared by
  // Dashboard Home, the Featured Projects tab, Edit Profile, onboarding,
  // the designers directory, and the public profile, so none of them
  // hand-roll their own project markup or CRUD calls. Self-contained on
  // purpose, same pattern as confirm-dialog.js: injects its own <style>
  // and DOM once per page load, reads the page's existing CSS custom
  // properties instead of redefining them, and manages its own auth/fetch
  // instead of depending on any page-scoped `token`/`api()` — several
  // pages that render project cards (designers.html, designer.html) are
  // logged-out pages with no such helpers.
  //
  // Usage:
  //   renderProjectCard(project, "manage"|"preview"|"compact"|"public")
  //   renderProjectsEmptyState({message, ctaLabel})
  //   openProjectForm(existingProjectOrNull) -> Promise<project|null>
  //   FeaturedProjects.deleteProject(id) -> Promise<void>
  //   FeaturedProjects.moveProject(projects, id, direction) -> Promise<project[]>
  //   FeaturedProjects.mount(containerEl, {projects, variant, limit, max, emptyMessage, onChange})
  if (window.renderProjectCard) return; // don't double-init if this ever loads twice

  var MAX_PROJECTS = 6;
  var TOKEN_KEY = "kazi_designer_token";
  var EMPTY_MESSAGE = "You haven't added any featured projects yet. Showcase your best work by adding up to six projects.";

  function ready(fn){
    if(document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  // ---- self-contained networking: reads the token directly out of
  // storage rather than relying on a page's own `token`/`api()` globals,
  // since public pages that render cards never define those at all. ----
  function fpToken(){ return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY) || ""; }
  function fpAuthHeader(){ var t = fpToken(); return t ? {"Authorization": "Bearer " + t} : {}; }
  async function fpHandle(res){
    if (!res.ok) {
      var body = await res.json().catch(function(){ return {}; });
      var err = new Error(window.PPErrorText ? PPErrorText(body, res.status)
        : (typeof body.detail === "string" ? body.detail : "Request failed (" + res.status + ")"));
      err.status = res.status;
      throw err;
    }
    return res.json();
  }
  async function fpApi(method, path, body){
    var res = await fetch(path, {
      method: method,
      headers: Object.assign({"Content-Type": "application/json"}, fpAuthHeader()),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    return fpHandle(res);
  }
  async function fpApiForm(method, path, formData){
    var res = await fetch(path, { method: method, headers: fpAuthHeader(), body: formData });
    return fpHandle(res);
  }

  // ---- icons: same 2px-stroke lucide-style language as every other page. ----
  var ICON_IMAGE = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.83 0L6 21"/></svg>';
  var ICON_PLUS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
  var ICON_EDIT = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497Z"/><path d="m15 5 4 4"/></svg>';
  var ICON_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>';
  var ICON_CHEVRON_UP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>';
  var ICON_CHEVRON_DOWN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
  var ICON_EXTERNAL = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>';
  var ICON_X = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';

  // ---- rendering ----
  function coverInner(project){
    if (project && project.image_path) {
      return '<img class="fp-cover-img" src="' + escapeHtml(project.image_path) + '" alt="">';
    }
    return '<div class="fp-cover-placeholder">' + ICON_IMAGE + '</div>';
  }

  function renderProjectCard(project, variant, meta){
    meta = meta || {};
    project = project || {};
    var title = '<p class="fp-title">' + escapeHtml(project.title || "") + '</p>';
    var desc = project.description ? '<p class="fp-desc">' + escapeHtml(project.description) + '</p>' : "";
    var cover = '<div class="fp-cover">' + coverInner(project) + '</div>';

    if (variant === "compact") {
      // Images only — no title/description/link — and optionally a "+N"
      // overlay on the last visible tile when the designer has more
      // projects than this grid shows (see designers.html, which caps
      // this variant at 3 and passes moreCount on the final one).
      var overlay = meta.moreCount ? '<div class="fp-more-overlay">+' + meta.moreCount + '</div>' : "";
      return '<div class="fp-card -compact">' + '<div class="fp-cover">' + coverInner(project) + overlay + '</div>' + '</div>';
    }

    if (variant === "public") {
      var url = project.url || "";
      var link = url ? '<a class="fp-link" href="' + escapeHtml(url) + '" target="_blank" rel="noopener">Visit project ' + ICON_EXTERNAL + '</a>' : "";
      return '<div class="fp-card -public"' + (url ? ' data-fp-open="' + escapeHtml(url) + '" role="link" tabindex="0"' : "") + '>' +
        cover + '<div class="fp-body">' + title + desc + link + '</div></div>';
    }

    var actions = "";
    if (variant === "manage") {
      var isFirst = meta.index === 0;
      var isLast = meta.index === (meta.total - 1);
      actions = '<div class="fp-actions">' +
        '<button type="button" class="fp-icon-btn" data-fp-up="' + project.id + '"' + (isFirst ? " disabled" : "") + ' aria-label="Move up">' + ICON_CHEVRON_UP + '</button>' +
        '<button type="button" class="fp-icon-btn" data-fp-down="' + project.id + '"' + (isLast ? " disabled" : "") + ' aria-label="Move down">' + ICON_CHEVRON_DOWN + '</button>' +
        '<button type="button" class="fp-icon-btn" data-fp-edit="' + project.id + '" aria-label="Edit">' + ICON_EDIT + '</button>' +
        '<button type="button" class="fp-icon-btn -danger" data-fp-delete="' + project.id + '" aria-label="Delete">' + ICON_TRASH + '</button>' +
      '</div>';
    }
    // "preview" is the same body as "manage" minus the action buttons —
    // used where space is tight (Dashboard Home) and editing happens elsewhere.
    return '<div class="fp-card">' + cover + '<div class="fp-body">' + title + desc + actions + '</div></div>';
  }

  function renderProjectsEmptyState(opts){
    opts = opts || {};
    var message = opts.message || EMPTY_MESSAGE;
    var ctaLabel = opts.ctaLabel || "Add a project";
    return '<div class="fp-empty">' +
      '<div class="fp-empty-cover">' + ICON_IMAGE + '</div>' +
      '<p class="fp-empty-text">' + escapeHtml(message) + '</p>' +
      '<button type="button" class="fp-add-btn" data-fp-empty-add>' + ICON_PLUS + ' ' + escapeHtml(ctaLabel) + '</button>' +
    '</div>';
  }

  window.renderProjectCard = renderProjectCard;
  window.renderProjectsEmptyState = renderProjectsEmptyState;

  // ---- shared CSS: card grid, empty state, and the add/edit form modal.
  // Prefixed fp-/fpf- to stay clear of every page's own classes; colors
  // and shape come from the page's own --brand/--surface/etc. vars. ----
  var STYLE = ""
    + ".fp-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}"
    + "@media(max-width:820px){.fp-grid{grid-template-columns:repeat(2,1fr)}}"
    + "@media(max-width:520px){.fp-grid{grid-template-columns:1fr}}"
    // .-compact adds a class beyond plain .fp-grid, so this specificity
    // (0,2,0) beats the two viewport media queries above (0,1,0) at every
    // width — the dcard thumbnail row stays 3-up on mobile/tablet/desktop
    // instead of collapsing to 2 or 1 columns like the other grids.
    + ".fp-grid.-compact{gap:6px;grid-template-columns:repeat(3,1fr)}"
    + ".fp-more-overlay{position:absolute;inset:0;background:rgba(0,0,0,.55);color:#fff;"
    + "display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:800}"
    + ".fp-card{display:flex;flex-direction:column;border:1px solid var(--hairline,#eee);border-radius:var(--radius,16px);"
    + "background:var(--surface,#fff);overflow:hidden}"
    + ".fp-card.-compact{border:0;background:none}"
    + ".fp-card.-public{cursor:pointer;transition:box-shadow .25s var(--ease-expo,ease),transform .25s var(--ease-expo,ease)}"
    + ".fp-card.-public:hover{box-shadow:var(--shadow,0 8px 24px rgba(0,0,0,.12));transform:translateY(-2px)}"
    + ".fp-cover{position:relative;width:100%;aspect-ratio:4/3;background:var(--hairline,#eee);overflow:hidden}"
    + ".fp-card:not(.-compact) .fp-cover{border-radius:var(--radius,16px) var(--radius,16px) 0 0}"
    + ".fp-card.-compact .fp-cover{border-radius:4px}"
    + ".fp-cover-img{width:100%;height:100%;object-fit:cover;display:block}"
    + ".fp-card.-compact .fp-cover-img{border-radius:4px}"
    + ".fp-cover-placeholder{width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:var(--faint,#999)}"
    + ".fp-body{display:flex;flex-direction:column;gap:6px;padding:14px 16px 16px}"
    + ".fp-card.-compact .fp-body{padding:8px 2px 0;gap:0}"
    + ".fp-title{font-size:14px;font-weight:700;color:var(--ink,#0B1623);margin:0}"
    + ".fp-card.-compact .fp-title{font-size:12.5px;font-weight:600}"
    + ".fp-desc{font-size:13px;color:var(--muted,#828282);line-height:1.5;margin:0;"
    + "display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}"
    + ".fp-link{display:inline-flex;align-items:center;gap:5px;font-size:12.5px;font-weight:700;color:var(--brand,#6D28A1);"
    + "text-decoration:none;margin-top:2px}"
    + ".fp-link:hover{text-decoration:underline}"
    + ".fp-actions{display:flex;gap:6px;margin-top:6px}"
    + ".fp-icon-btn{background:none;border:1px solid var(--hairline-strong,#ccc);color:var(--muted,#828282);"
    + "width:30px;height:30px;border-radius:8px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;"
    + "transition:border-color .2s var(--ease-expo,ease),color .2s var(--ease-expo,ease)}"
    + ".fp-icon-btn:hover{border-color:var(--brand,#6D28A1);color:var(--brand,#6D28A1)}"
    + ".fp-icon-btn.-danger:hover{border-color:var(--red,#D3322A);color:var(--red,#D3322A)}"
    + ".fp-icon-btn:disabled{opacity:.35;cursor:not-allowed}"
    + ".fp-icon-btn:disabled:hover{border-color:var(--hairline-strong,#ccc);color:var(--muted,#828282)}"
    + ".fp-add-btn{align-self:flex-start;display:inline-flex;align-items:center;gap:7px;background:none;"
    + "border:1px dashed var(--hairline-strong,#ccc);color:var(--muted,#828282);font:inherit;font-size:13px;font-weight:700;"
    + "padding:10px 18px;border-radius:10px;cursor:pointer;margin-top:16px}"
    + ".fp-add-btn:hover{border-color:var(--brand,#6D28A1);color:var(--brand,#6D28A1)}"
    + ".fp-add-btn:disabled{opacity:.45;cursor:not-allowed}"
    + ".fp-empty{display:flex;flex-direction:column;align-items:center;text-align:center;padding:32px 20px;"
    + "border:1px solid var(--hairline,#eee);border-radius:var(--radius,16px)}"
    + ".fp-empty-cover{width:100%;max-width:220px;aspect-ratio:4/3;border-radius:12px;background:var(--hairline,#eee);"
    + "display:flex;align-items:center;justify-content:center;color:var(--faint,#999);margin-bottom:16px}"
    + ".fp-empty-text{font-size:13.5px;color:var(--muted,#828282);line-height:1.5;max-width:360px;margin:0 0 4px}"
    + ".fp-empty .fp-add-btn{margin-top:14px}"
    // ---- add/edit form: same modal/bottom-sheet shape as confirm-dialog.js ----
    + ".fpf-backdrop{position:fixed;inset:0;z-index:900;background:rgba(0,0,0,.5);"
    + "display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box;"
    + "opacity:0;pointer-events:none;transition:opacity .25s var(--ease-expo,ease)}"
    + ".fpf-backdrop.-open{opacity:1;pointer-events:auto}"
    + ".fpf-panel{background:var(--surface,#fff);border-radius:var(--radius,16px);"
    + "box-shadow:var(--shadow,0 8px 24px rgba(0,0,0,.18));max-width:440px;width:100%;max-height:88vh;overflow-y:auto;"
    + "padding:26px;box-sizing:border-box;transform:scale(.94) translateY(8px);opacity:0;"
    + "transition:transform .25s var(--ease-expo,ease),opacity .25s var(--ease-expo,ease)}"
    + ".fpf-backdrop.-open .fpf-panel{transform:scale(1) translateY(0);opacity:1}"
    + ".fpf-handle{display:none}"
    + ".fpf-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}"
    + ".fpf-title{font-size:16px;font-weight:800;margin:0;color:var(--ink,#0B1623)}"
    + ".fpf-close{background:none;border:0;color:var(--muted,#828282);cursor:pointer;padding:4px;line-height:0}"
    + ".fpf-close:hover{color:var(--ink,#0B1623)}"
    + ".fpf-cover-picker{width:100%;aspect-ratio:4/3;border-radius:12px;background:var(--hairline,#eee);"
    + "border:1px dashed var(--hairline-strong,#ccc);cursor:pointer;display:flex;align-items:center;justify-content:center;"
    + "overflow:hidden;position:relative;margin-bottom:16px}"
    + ".fpf-cover-picker img{width:100%;height:100%;object-fit:cover;display:block}"
    + ".fpf-cover-picker:hover{border-color:var(--brand,#6D28A1)}"
    + ".fpf-cover-hint{display:flex;flex-direction:column;align-items:center;gap:6px;color:var(--faint,#999);font-size:12px;font-weight:600}"
    + ".fpf-field{display:flex;flex-direction:column;gap:6px;font-size:13.5px;font-weight:600;color:var(--ink,#0B1623);margin-bottom:14px}"
    + ".fpf-field input,.fpf-field textarea{font:inherit;font-size:14px;font-weight:400;padding:11px 13px;"
    + "border:1px solid var(--hairline-strong,#ccc);border-radius:10px;background:var(--surface,#fff);color:var(--ink,#0B1623);"
    + "box-sizing:border-box;width:100%;resize:vertical}"
    + ".fpf-field input:focus,.fpf-field textarea:focus{outline:0;border-color:var(--brand,#6D28A1)}"
    + ".fpf-err{font-size:13px;color:var(--red,#D3322A);margin:0 0 14px;display:none}"
    + ".fpf-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:6px}"
    + ".fpf-btn{font:inherit;font-size:14px;font-weight:700;padding:11px 20px;border-radius:999px;cursor:pointer;"
    + "transition:background .25s var(--ease-expo,ease),border-color .25s var(--ease-expo,ease),opacity .15s ease}"
    + ".fpf-btn-cancel{background:none;border:1px solid var(--hairline-strong,#ccc);color:var(--ink,#0B1623)}"
    + ".fpf-btn-cancel:hover{border-color:var(--brand,#6D28A1)}"
    + ".fpf-btn-save{background:var(--brand,#6D28A1);border:1px solid transparent;color:#fff}"
    + ".fpf-btn-save:hover{background:var(--brand-light,#AA52ED)}"
    + ".fpf-btn:disabled{opacity:.6;cursor:not-allowed}"
    + "@media(max-width:600px){"
    + ".fpf-backdrop{align-items:flex-end;padding:0}"
    + ".fpf-panel{max-width:none;border-radius:20px 20px 0 0;padding:14px 20px 24px;max-height:92vh}"
    + ".fpf-backdrop.-open .fpf-panel{transform:translateY(0)}"
    + ".fpf-handle{display:block;width:36px;height:4px;border-radius:999px;background:var(--hairline-strong,#ccc);margin:0 auto 16px}"
    + "}";

  var formBackdrop, coverPicker, coverImg, coverHint, fileInput, titleInput, descInput, urlInput, categoryInput, errEl, saveBtn, formTitleEl;
  var formResolve = null, lastFocused = null, stagedFile = null, currentProjectId = null;

  function onFormKeydown(e){
    if (e.key === "Escape") closeForm(null);
  }

  function closeForm(result){
    if (!formBackdrop.classList.contains("-open")) return;
    formBackdrop.classList.remove("-open");
    document.removeEventListener("keydown", onFormKeydown);
    if (lastFocused && lastFocused.focus) lastFocused.focus();
    if (stagedFile && coverImg.dataset.blobUrl) URL.revokeObjectURL(coverImg.dataset.blobUrl);
    stagedFile = null;
    if (formResolve) { var r = formResolve; formResolve = null; r(result); }
  }

  function showErr(msg){
    errEl.textContent = msg;
    errEl.style.display = "block";
  }

  function updateCoverPreview(url){
    if (url) {
      coverImg.src = url;
      coverImg.style.display = "block";
      coverHint.style.display = "none";
    } else {
      coverImg.style.display = "none";
      coverImg.removeAttribute("src");
      coverHint.style.display = "flex";
    }
  }

  async function handleSave(){
    var title = titleInput.value.trim();
    if (!title) { showErr("Title is required."); titleInput.focus(); return; }
    var url = urlInput.value.trim();
    if (url && !/^https?:\/\//i.test(url)) { showErr("Project URL must start with http:// or https://"); urlInput.focus(); return; }
    errEl.style.display = "none";
    saveBtn.disabled = true;
    try {
      var payload = { title: title, description: descInput.value.trim(), url: url, category: categoryInput.value.trim() };
      var project = currentProjectId
        ? await fpApi("PUT", "/api/designers/me/projects/" + currentProjectId, payload)
        : await fpApi("POST", "/api/designers/me/projects", payload);
      if (stagedFile) {
        var fd = new FormData();
        fd.append("file", stagedFile);
        project = await fpApiForm("POST", "/api/designers/me/projects/" + project.id + "/image", fd);
      }
      closeForm(project);
    } catch (err) {
      showErr(err.message || "Something went wrong. Please try again.");
    } finally {
      saveBtn.disabled = false;
    }
  }

  function openProjectForm(existing){
    return new Promise(function(resolve){
      formResolve = resolve;
      stagedFile = null;
      currentProjectId = existing ? existing.id : null;
      formTitleEl.textContent = existing ? "Edit project" : "Add featured project";
      titleInput.value = existing ? (existing.title || "") : "";
      descInput.value = existing ? (existing.description || "") : "";
      urlInput.value = existing ? (existing.url || "") : "";
      categoryInput.value = existing ? (existing.category || "") : "";
      errEl.style.display = "none";
      updateCoverPreview(existing && existing.image_path ? existing.image_path : null);
      lastFocused = document.activeElement;
      document.addEventListener("keydown", onFormKeydown);
      (window.PPRaf || requestAnimationFrame)(function(){
        formBackdrop.classList.add("-open");
        titleInput.focus();
      });
    });
  }
  window.openProjectForm = openProjectForm;

  ready(function(){
    var styleEl = document.createElement("style");
    styleEl.textContent = STYLE;
    document.head.appendChild(styleEl);

    formBackdrop = document.createElement("div");
    formBackdrop.className = "fpf-backdrop";
    formBackdrop.innerHTML =
      '<div class="fpf-panel" role="dialog" aria-modal="true" aria-labelledby="fpfTitle">' +
        '<div class="fpf-handle"></div>' +
        '<div class="fpf-head"><p class="fpf-title" id="fpfTitle"></p>' +
          '<button type="button" class="fpf-close" id="fpfCloseBtn" aria-label="Close">' + ICON_X + '</button></div>' +
        '<button type="button" class="fpf-cover-picker" id="fpfCoverPicker">' +
          '<img id="fpfCoverImg" style="display:none" alt="">' +
          '<span class="fpf-cover-hint" id="fpfCoverHint">' + ICON_IMAGE + '<span>Add a cover image</span></span>' +
        '</button>' +
        '<input type="file" id="fpfFileInput" accept="image/jpeg,image/png,image/webp" style="display:none">' +
        '<p class="fpf-err" id="fpfErr"></p>' +
        '<label class="fpf-field"><span>Title</span><input type="text" id="fpfTitleInput" maxlength="80" placeholder="Project name"></label>' +
        '<label class="fpf-field"><span>Short description</span><textarea id="fpfDescInput" rows="3" maxlength="300" placeholder="What is this project?"></textarea></label>' +
        '<label class="fpf-field"><span>Project URL <span style="font-weight:400;color:var(--faint,#999)">(optional)</span></span><input type="url" id="fpfUrlInput" maxlength="500" placeholder="https://"></label>' +
        '<label class="fpf-field"><span>Category <span style="font-weight:400;color:var(--faint,#999)">(optional)</span></span><input type="text" id="fpfCategoryInput" maxlength="40" placeholder="e.g. Branding"></label>' +
        '<div class="fpf-actions">' +
          '<button type="button" class="fpf-btn fpf-btn-cancel" id="fpfCancelBtn">Cancel</button>' +
          '<button type="button" class="fpf-btn fpf-btn-save" id="fpfSaveBtn">Save</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(formBackdrop);

    coverPicker = document.getElementById("fpfCoverPicker");
    coverImg = document.getElementById("fpfCoverImg");
    coverHint = document.getElementById("fpfCoverHint");
    fileInput = document.getElementById("fpfFileInput");
    titleInput = document.getElementById("fpfTitleInput");
    descInput = document.getElementById("fpfDescInput");
    // Visible limit: this field silently swallowed the end of a description.
    if (window.PPCount) { PPCount(descInput); PPCount(document.getElementById("fpfTitleInput")); }
    urlInput = document.getElementById("fpfUrlInput");
    categoryInput = document.getElementById("fpfCategoryInput");
    errEl = document.getElementById("fpfErr");
    saveBtn = document.getElementById("fpfSaveBtn");
    formTitleEl = document.getElementById("fpfTitle");

    coverPicker.addEventListener("click", function(){ fileInput.click(); });
    fileInput.addEventListener("change", function(){
      var file = fileInput.files && fileInput.files[0];
      if (!file) return;
      stagedFile = file;
      var blobUrl = URL.createObjectURL(file);
      coverImg.dataset.blobUrl = blobUrl;
      updateCoverPreview(blobUrl);
    });
    formBackdrop.addEventListener("mousedown", function(e){ if (e.target === formBackdrop) closeForm(null); });
    document.getElementById("fpfCloseBtn").addEventListener("click", function(){ closeForm(null); });
    document.getElementById("fpfCancelBtn").addEventListener("click", function(){ closeForm(null); });
    saveBtn.addEventListener("click", handleSave);

    // ---- global delegated handling for "public" cards: the whole card is
    // clickable, but the visible <a> inside it handles its own navigation,
    // so this only fires window.open() when the click landed outside that
    // inner link (and is never itself an <a>, which would be invalid HTML
    // nested inside the directory card's own outer <a>). ----
    document.addEventListener("click", function(e){
      var card = e.target.closest("[data-fp-open]");
      if (!card || e.target.closest("a")) return;
      var url = card.getAttribute("data-fp-open");
      if (url) window.open(url, "_blank", "noopener");
    });
    document.addEventListener("keydown", function(e){
      if (e.key !== "Enter" && e.key !== " ") return;
      var card = e.target.closest && e.target.closest("[data-fp-open]");
      if (!card || e.target.tagName === "A") return;
      e.preventDefault();
      var url = card.getAttribute("data-fp-open");
      if (url) window.open(url, "_blank", "noopener");
    });
  });

  // ---- manage-grid controller: owns rendering + wiring for any
  // interactive "manage" surface (Featured Projects tab, Edit Profile,
  // onboarding), and the read-only "preview" surface (Dashboard Home),
  // so none of them re-implement add/edit/delete/reorder plumbing. ----
  function mount(container, opts){
    opts = opts || {};
    var max = opts.max || MAX_PROJECTS;
    var variant = opts.variant || "manage";
    var limit = opts.limit || null;
    var onChange = opts.onChange || function(){};
    var projects = (opts.projects || []).slice().sort(function(a,b){ return a.sort_order - b.sort_order; });

    function render(){
      if (!projects.length) {
        container.innerHTML = renderProjectsEmptyState({ message: opts.emptyMessage, ctaLabel: opts.ctaLabel });
      } else {
        var shown = limit ? projects.slice(0, limit) : projects;
        var cardsHtml = shown.map(function(p){
          return renderProjectCard(p, variant, { index: projects.indexOf(p), total: projects.length });
        }).join("");
        var addBtn = variant === "manage"
          ? '<button type="button" class="fp-add-btn" data-fp-add' + (projects.length >= max ? " disabled" : "") + '>' + ICON_PLUS + ' Add a project</button>'
          : "";
        container.innerHTML = '<div class="fp-grid">' + cardsHtml + '</div>' + addBtn;
      }
      wire();
    }

    function wire(){
      var addBtns = container.querySelectorAll("[data-fp-add], [data-fp-empty-add]");
      addBtns.forEach(function(btn){ btn.addEventListener("click", handleAdd); });
      if (variant !== "manage") return;
      container.querySelectorAll("[data-fp-edit]").forEach(function(btn){
        btn.addEventListener("click", function(){ handleEdit(btn.getAttribute("data-fp-edit")); });
      });
      container.querySelectorAll("[data-fp-delete]").forEach(function(btn){
        btn.addEventListener("click", function(){ handleDelete(btn.getAttribute("data-fp-delete")); });
      });
      container.querySelectorAll("[data-fp-up]").forEach(function(btn){
        btn.addEventListener("click", function(){ handleMove(btn.getAttribute("data-fp-up"), -1); });
      });
      container.querySelectorAll("[data-fp-down]").forEach(function(btn){
        btn.addEventListener("click", function(){ handleMove(btn.getAttribute("data-fp-down"), 1); });
      });
    }

    async function handleAdd(){
      if (projects.length >= max) return;
      var saved = await openProjectForm(null);
      if (!saved) return;
      projects.push(saved);
      render();
      onChange(projects.slice());
    }
    async function handleEdit(id){
      var existing = projects.find(function(p){ return String(p.id) === String(id); });
      if (!existing) return;
      var saved = await openProjectForm(existing);
      if (!saved) return;
      projects = projects.map(function(p){ return String(p.id) === String(saved.id) ? saved : p; });
      render();
      onChange(projects.slice());
    }
    async function handleDelete(id){
      var ok = await window.showConfirmDialog({
        title: "Delete this project?",
        description: "This can't be undone.",
        confirmLabel: "Delete",
        danger: true,
      });
      if (!ok) return;
      try {
        await FeaturedProjects.deleteProject(id);
      } catch (err) {
        await window.showAlertDialog({ title: "Couldn't delete project", description: err.message || "Please try again." });
        return;
      }
      projects = projects.filter(function(p){ return String(p.id) !== String(id); });
      render();
      onChange(projects.slice());
    }
    async function handleMove(id, direction){
      try {
        projects = await FeaturedProjects.moveProject(projects, id, direction);
      } catch (err) { /* best-effort; grid stays as-is on failure */ }
      render();
      onChange(projects.slice());
    }

    render();
    return {
      setProjects: function(list){ projects = (list || []).slice().sort(function(a,b){ return a.sort_order - b.sort_order; }); render(); },
      getProjects: function(){ return projects.slice(); },
    };
  }

  var FeaturedProjects = {
    MAX_PROJECTS: MAX_PROJECTS,
    deleteProject: function(id){
      return fpApi("DELETE", "/api/designers/me/projects/" + id);
    },
    moveProject: async function(projects, id, direction){
      var idx = projects.findIndex(function(p){ return String(p.id) === String(id); });
      var newIdx = idx + direction;
      if (idx < 0 || newIdx < 0 || newIdx >= projects.length) return projects.slice();
      var reordered = projects.slice();
      var tmp = reordered[idx]; reordered[idx] = reordered[newIdx]; reordered[newIdx] = tmp;
      await fpApi("PUT", "/api/designers/me/projects/reorder", { ids: reordered.map(function(p){ return p.id; }) });
      return reordered;
    },
    mount: mount,
  };
  window.FeaturedProjects = FeaturedProjects;
})();
