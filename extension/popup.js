"use strict";

const $ = (id) => document.getElementById(id);
const send = (msg) => browser.runtime.sendMessage(msg);
const EMBED = /player\.vimeo\.com|dailymotion\.com\/(embed|player)|player\.twitch\.tv|streamable\.com\/[eo]\/|facebook\.com\/plugins\/video|tiktok\.com\/embed|rumble\.com\/embed|player\.bilibili\.com|archive\.org\/embed/;
const ALL_LANGS = ["en", "en-US", "en-GB", "es", "es-ES", "es-419", "pt", "pt-BR", "pt-PT", "fr", "fr-CA", "de", "it", "nl",
  "pl", "ru", "uk", "tr", "ar", "he", "fa", "hi", "bn", "ur", "ta", "te", "id", "ms", "vi", "th", "tl", "ja", "ko",
  "zh-Hans", "zh-Hant", "ca", "eu", "gl", "sv", "no", "da", "fi", "is", "el", "cs", "sk", "ro", "hu", "bg", "hr", "sr",
  "sl", "lt", "lv", "et", "sw"];
const AI_MODELS = {openai: "gpt-4o-mini", gemini: "gemini-2.5-flash",
  openrouter: "openrouter/auto", ollama: "llama3.2", custom: ""};
const INSTALL_COMMAND = "irm https://raw.githubusercontent.com/C-Rosende/Tomo/5c3971e394278778990092966bc311383dd7edcd/get.ps1 | iex";
let imageAiRevision = 0;

async function renderImageAi() {
  const {pictureAi: imageAi} = await browser.storage.local.get("pictureAi");
  const active = !!(imageAi?.consent && imageAi?.key && imageAiProvider(imageAi.key) === imageAi.provider);
  $("image-ai-status").textContent = active ? `${t("imageAiActive")} · ${imageAi.provider === "replicate" ? "Replicate" : "Gemini"} · ${imageAiMask(imageAi.key)}` : "";
  $("image-ai-remove").hidden = !imageAi?.key;
  $("image-ai-key").closest("label").hidden = active;
  $("image-ai-save").hidden = active;
  $("image-ai-remove").title = t("imageAiRemove");
  $("go-image-ai").hidden = !active;
  $("image-ai-quality").checked = !!imageAi?.quality;
}

let state = {tab: null, candidates: [], current: null, info: null, backend: null, prefs: null, jobs: [], history: [],
  view: "video", videoLoaded: false, pending: null, donate: {}, mainLang: "en", multi: false, picked: new Set()};

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c);
  return el;
}

function applyI18n(root = document) {
  document.documentElement.lang = I18N.lang;
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll("[data-i18n-aria]")) el.setAttribute("aria-label", t(el.dataset.i18nAria));
  for (const el of root.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
}

const ICONS = {x: "M18 6 6 18M6 6l12 12", plus: "M12 5v14M5 12h14"};
function icon(name) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

const progressBar = (percent, label) => h("div", {class: `track${percent == null ? " indeterminate" : ""}`, role: "progressbar", "aria-label": label,
  "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": percent == null ? null : String(Math.round(percent))},
h("div", {class: "fill", style: percent == null ? null : `width: ${Math.max(2, percent)}%`}));

const isHttp = (u) => /^https?:\/\//i.test(u || "");
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (_) { return ""; } };
const fileOf = (u) => { try { return decodeURIComponent(new URL(u).pathname.split("/").pop()) || u; } catch (_) { return u; } };
const radio = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;
const option = (value, label, selected) => h("option", {value, selected: Boolean(selected)}, label);
const capital = (s, locale) => s.charAt(0).toLocaleUpperCase(locale) + s.slice(1);
const fold = (s) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
const primary = (c) => String(c).split("-")[0].toLowerCase();

function langNames(code) {
  const name = (locale) => {
    try { return capital(new Intl.DisplayNames([locale], {type: "language", languageDisplay: "standard"}).of(code), locale); }
    catch (_) { return ""; }
  };
  return {native: name(code) || code, local: name(I18N.lang)};
}

function langFits(code, pref) {
  const c = String(code).toLowerCase(), p = String(pref).toLowerCase();
  return c === p || c.startsWith(p + "-") || p.startsWith(c + "-");
}

const myLangIndex = (code) => (state.prefs?.langs || []).findIndex((pref) => langFits(code, pref));

function langPicker(root, initial, onChange, suggest = false) {
  const main = state.mainLang;
  let extra = initial.filter((c) => primary(c) !== main);
  const tags = h("div", {class: "tags"});
  const search = h("input", {type: "search", placeholder: t("addLanguage"), "aria-label": t("addLanguage"), spellcheck: "false"});
  const suggestTitle = h("p", {class: "suggest-title"}, t("suggested"));
  const results = h("div", {class: "results", role: "listbox"});
  const all = () => [main, ...extra];
  const set = (list) => { extra = list; render(); onChange(all()); };
  function render() {
    tags.replaceChildren(
      h("span", {class: "tag main"}, langNames(main).native, h("small", {}, t("mainLanguage"))),
      ...extra.map((code) => h("span", {class: "tag"}, langNames(code).native,
        h("button", {type: "button", "aria-label": t("removeLang", langNames(code).native), title: t("removeLang", langNames(code).native),
          onclick: () => set(extra.filter((c) => c !== code))}, icon("x")))));
    const q = fold(search.value.trim());
    const pool = ALL_LANGS.filter((c) => primary(c) !== main && !extra.includes(c));
    const base = (navigator.languages || []).map(primary);
    const found = q ? pool.filter((c) => { const n = langNames(c); return fold(`${n.native} ${n.local} ${c}`).includes(q); }).slice(0, 30)
      : !suggest ? [] : [...new Set([...base, "en", "es", "fr", "pt", "de"])].filter((c) => pool.includes(c)).slice(0, 4);
    suggestTitle.hidden = Boolean(q) || !found.length;
    results.replaceChildren(...found.map((code) => h("button", {type: "button", role: "option",
      onclick: () => { search.value = ""; set([...extra, code]); search.focus(); }},
    h("span", {}, langNames(code).native), h("span", {class: "add"}, icon("plus")))));
    if (q && !found.length) results.replaceChildren(h("p", {class: "muted small pad"}, t("noResults")));
  }
  search.addEventListener("input", render);
  search.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); results.querySelector("button")?.click(); }
  });
  root.replaceChildren(tags, search, suggestTitle, results);
  render();
}

function langChips(container, name, fixed, codes) {
  const labels = new Set(fixed.map(([, l]) => l));
  const mine = [];
  for (const pref of state.prefs?.langs || []) {
    const fits = codes.filter((c) => !mine.includes(c) && langFits(c, pref));
    const best = fits.find((c) => c.toLowerCase() === pref.toLowerCase()) || fits[0];
    if (best && !labels.has(langNames(best).native)) { mine.push(best); labels.add(langNames(best).native); }
  }
  const chip = (value, label, checked, title = "") => h("label", {class: "chip", title},
    h("input", {type: "radio", name, value, checked}), h("span", {}, h("b", {}, label)));
  container.replaceChildren(...fixed.map(([v, l, tip], i) => chip(v, l, i === 0, tip)), ...mine.map((c) => chip(c, langNames(c).native, false)));
  return mine.length;
}

function clock(s) {
  if (!s && s !== 0) return "";
  s = Math.round(s);
  const hh = Math.floor(s / 3600), mm = Math.floor(s % 3600 / 60), ss = s % 60;
  return (hh ? `${hh}:${String(mm).padStart(2, "0")}` : `${mm}`) + `:${String(ss).padStart(2, "0")}`;
}

function bytes(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${(n >= 100 || i < 2 ? Math.round(n) : Number(n.toFixed(1))).toLocaleString(I18N.lang)} ${u[i]}`;
}

function showStatus(text) {
  $("status").textContent = text || "";
  $("status").hidden = !text;
}

function note(id, text, warn = false) {
  $(id).textContent = text || "";
  $(id).hidden = !text;
  $(id).classList.toggle("warn", warn);
}

const resolutionName = (q) => !q.height ? t("original") : q.height >= 4320 ? "8K" : q.height >= 2160 ? "4K" : `${q.height}p`;

function showView(name) {
  state.view = name;
  for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== `view-${name}`;
  $("nav").hidden = !["video", "jobs"].includes(name);
  for (const b of $("nav").querySelectorAll("button")) {
    b.setAttribute("aria-selected", String(b.dataset.view === name));
    b.tabIndex = b.dataset.view === name ? 0 : -1;
  }
  $("back").hidden = name !== "settings";
  $("open-settings").hidden = name === "settings" || name === "welcome";
  $("donate-top").hidden = !state.donate.donateUrl || name === "welcome";
  $("heading").textContent = name === "settings" ? t("settings") : "Tomo";
  window.scrollTo({top: 0});
  if (name === "video" && !state.videoLoaded) loadVideo();
}

function openSettingsAt(id, message) {
  state.back = state.view;
  showView("settings");
  $(id).scrollIntoView({block: "start"});
  if (message) $("backend-status").textContent = message;
}

function buildCandidates(frames, tab, pending) {
  const list = [];
  const add = (c) => {
    if (platform.blocks(c.target.url) || platform.blocks(c.target.src_url) || platform.blocks(c.target.page_url)) return;
    if (!list.some((x) => x.target.url === c.target.url && (x.target.src_url || "") === (c.target.src_url || ""))) list.push(c);
  };
  const top = frames.find((f) => f.top);
  if (pending?.target) add({title: pending.title || t("pointedVideo"), sub: t("pointedSub"), target: pending.target,
    preview: platform.preview(pending.target.url)});
  if (isHttp(tab?.url) && !isImagePage(tab.url, top)) add({title: tab.title || hostOf(tab.url), sub: hostOf(tab.url),
    target: {url: tab.url, page_url: "", src_url: ""}, preview: platform.preview(tab.url, top?.preview)});
  for (const f of frames) {
    if (!f.top && isHttp(f.url) && (f.media.length || EMBED.test(f.url) || platform.embed(f.url))) {
      add({title: t("embeddedVideo"), sub: hostOf(f.url), target: {url: f.url, page_url: tab.url, src_url: ""}});
    }
    for (const m of f.media) {
      if (!m.src) continue;
      const size = m.width ? ` · ${m.width}×${m.height}` : "";
      add({title: m.label || fileOf(m.src), sub: `${m.kind === "audio" ? t("kindAudio") : t("kindVideo")}${size}`,
        target: {url: m.src, page_url: isHttp(f.url) ? f.url : "", src_url: ""}});
    }
  }
  for (const u of top?.frames || []) {
    if (EMBED.test(u) || platform.embed(u)) add({title: t("embeddedVideo"), sub: hostOf(u), target: {url: u, page_url: tab.url, src_url: ""}});
  }
  return list;
}

function isImagePage(url, frame) {
  return /^image\//i.test(frame?.contentType || "") || /\.(?:avif|bmp|gif|jpe?g|png|svg|webp)(?:[?#]|$)/i.test(url || "");
}

function renderCandidates() {
  const multi = state.multi;
  $("candidates").replaceChildren(...state.candidates.map((c, i) => h("li", {}, multi
    ? h("label", {class: "pick check"},
      h("input", {type: "checkbox", checked: state.picked.has(i), onchange: (e) => {
        if (e.target.checked) state.picked.add(i); else state.picked.delete(i);
        renderBatch();
      }}),
      h("span", {}, h("span", {class: "t"}, c.title), h("span", {class: "s"}, c.sub)))
    : h("button", {type: "button", class: "pick", "aria-current": state.current === c ? "true" : "false", onclick: () => select(c)},
      h("span", {class: "t"}, c.title), h("span", {class: "s"}, c.sub)))));
  $("candidates-section").hidden = state.candidates.length < 2;
  $("select-many-label").textContent = multi ? t("selectDone") : t("selectMany");
  renderBatch();
}

function renderBatch() {
  const multi = state.multi;
  $("batch-footer").hidden = !multi;
  $("detail").hidden = multi || !state.current;
  $("video-footer").hidden = multi || !state.info;
  $("batch-go").textContent = t("batchGo", state.picked.size);
  $("batch-go").disabled = !state.picked.size;
}

function toggleMulti() {
  state.multi = !state.multi;
  state.picked = new Set(state.multi ? state.candidates.map((_, i) => i) : []);
  renderCandidates();
}

async function startBatch() {
  const preset = $("batch-preset").value;
  const request = preset === "txt" ? {action: "transcript", mode: "export", format: "txt"} : {action: "download", preset};
  const items = [...state.picked].map((i) => state.candidates[i]).map((c) => ({target: c.target, title: c.title}));
  try {
    await send({type: "startBatch", items, request});
    state.multi = false;
    state.picked.clear();
    renderCandidates();
    showView("jobs");
  } catch (err) {
    showStatus(err.message);
  }
}

async function loadVideo() {
  state.videoLoaded = true;
  const [tab] = await browser.tabs.query({active: true, currentWindow: true});
  state.tab = tab;
  const recommend = (kind) => {
    const labels = {Image: t("recommendImage"), Audio: t("recommendAudio"), Video: t("recommendVideo"), Text: t("recommendText")};
    $("site-recommendation").textContent = isHttp(tab?.url) ? labels[kind] : "";
  };
  if (platform.blocks(tab?.url)) {
    $("setup").hidden = true;
    showStatus(t("siteUnavailable"));
    return;
  }
  if (isImagePage(tab?.url)) {
    recommend("Image");
    $("image-panel").hidden = false;
    return;
  }
  if (/\.(?:mp3|m4a|aac|ogg|wav|flac)(?:[?#]|$)/i.test(tab?.url || "")) recommend("Audio");
  else recommend("Video");
  if (state.backend?.error) return;
  const guess = buildCandidates([], tab, state.pending)[0];
  const frames = tab ? await send({type: "detect", tabId: tab.id}).catch(() => []) : [];
  if (!(frames || []).some((f) => f.media?.length) && !/(?:vimeo|tiktok|twitch|dailymotion|rumble)/i.test(hostOf(tab?.url || ""))
      && !/\.(?:mp3|m4a|aac|ogg|wav|flac)(?:[?#]|$)/i.test(tab?.url || "")) recommend("Text");
  if (isImagePage(tab?.url, (frames || []).find((f) => f.top))) {
    recommend("Image");
    state.current = state.info = null;
    state.candidates = [];
    renderCandidates();
    $("image-panel").hidden = false;
    return;
  }
  state.candidates = buildCandidates(frames || [], tab, state.pending);
  if (!state.candidates.length) recommend("Text");
  const same = (c) => guess && c.target.url === guess.target.url && (c.target.src_url || "") === (guess.target.src_url || "");
  const index = state.candidates.findIndex(same);
  if (index >= 0) {
    const found = state.candidates[index];
    if (!guess.preview?.image && found.preview?.image) {
      guess.preview = found.preview;
      if (state.current === guess && !state.info) showThumb(guess.preview.image);
    }
    state.candidates[index] = guess;
  }
  renderCandidates();
  if (!state.candidates.length) return showStatus(t("noVideo"));
  await select(state.candidates[0]);
  const view = state.pending?.view;
  if (["video", "audio", "transcript"].includes(view)) selectTab(view);
}

function showThumb(url) {
  $("thumb").replaceChildren(...(isHttp(url) ? [h("img", {src: url, alt: ""})] : []));
  $("thumb").hidden = !isHttp(url);
  $("thumb-ph").hidden = isHttp(url);
}

async function select(candidate) {
  $("retry-resolve").hidden = true;
  state.current = candidate;
  state.info = null;
  renderCandidates();
  $("detail").hidden = false;
  $("detail-body").hidden = $("video-footer").hidden = true;
  $("detail-loading").hidden = false;
  $("detail-loading").setAttribute("aria-label", t("loading"));
  $("title").textContent = candidate.preview?.title || candidate.title;
  $("meta").textContent = candidate.sub;
  $("duration").hidden = true;
  showThumb(candidate.preview?.image);
  showStatus("");
  try {
    const result = await send({type: "resolve", target: candidate.target});
    if (state.current !== candidate) return;
    if (result.info.kind === "playlist") return showPlaylist(candidate, result.info);
    state.info = result.info;
    renderDetail();
  } catch (_) {
    if (state.current !== candidate) return;
    $("detail").hidden = $("detail-loading").hidden = $("thumb-ph").hidden = true;
    showStatus(t("resolveFailed"));
    $("retry-resolve").hidden = false;
  }
}

function showPlaylist(candidate, info) {
  $("detail-loading").hidden = $("thumb-ph").hidden = true;
  $("meta").textContent = t("videosPickOne", info.entries.length);
  for (const e of info.entries) {
    const c = {title: e.title, sub: [hostOf(e.url), clock(e.duration)].filter(Boolean).join(" · "),
      target: {url: e.url, page_url: candidate.target.url, src_url: ""}};
    if (!state.candidates.some((x) => x.target.url === c.target.url)) state.candidates.push(c);
  }
  renderCandidates();
}

function qualityOf() {
  return (state.info?.qualities || [])[Number(radio("quality")) || 0];
}

function qualityChip(q, i, audioSize) {
  const size = q.size ? `${q.approx ? "≈ " : ""}${bytes(q.size + (q.has_audio ? 0 : audioSize))}` : "";
  const extra = [q.fps60 ? `${q.fps} fps` : "", q.hdr ? "HDR" : ""].filter(Boolean).join(" ");
  return h("label", {class: "chip", title: q.label},
    h("input", {type: "radio", name: "quality", value: String(i), checked: i === 0}),
    h("span", {}, h("b", {}, resolutionName(q)), h("small", {}, [extra, size].filter(Boolean).join(" · ") || (i === 0 ? t("maxQuality") : ""))));
}

function renderDetail() {
  const info = state.info;
  $("detail-loading").hidden = true;
  $("detail-body").hidden = $("video-footer").hidden = false;
  $("title").textContent = info.title || state.current.title;
  $("meta").textContent = [info.uploader, hostOf(info.webpage_url) || hostOf(state.current.target.url),
    info.live ? t("live") : ""].filter(Boolean).join(" · ");
  $("thumb").replaceChildren(...(isHttp(info.thumbnail) ? [h("img", {src: info.thumbnail, alt: ""})] : []));
  $("thumb").hidden = !isHttp(info.thumbnail);
  $("thumb-ph").hidden = true;
  $("duration").textContent = clock(info.duration);
  $("duration").hidden = $("thumb").hidden || !info.duration;

  const qualities = info.qualities || [];
  const audioSize = (info.audios || [])[0]?.size || 0;
  $("quality").replaceChildren(...qualities.map((q, i) => qualityChip(q, i, audioSize)));

  const audioOriginal = info.audio_original;
  const langs = (info.audio_langs || []).filter((c) => !audioOriginal || primary(c) !== primary(audioOriginal));
  const audioFirst = [["", t("original"), audioOriginal ? langNames(audioOriginal).native : ""]];
  $("audio-lang-field").hidden = !langChips($("audio-lang"), "audio-lang", audioFirst, langs);
  $("audio-lang-field2").hidden = !langChips($("audio-lang2"), "audio-lang2", audioFirst, langs);
  $("tab-audio").hidden = !(info.audios || []).length && !qualities.some((q) => q.has_audio);

  const subs = info.subtitles || {languages: []};
  const others = subs.languages.map((l) => l.code).filter((c) => !subs.original || primary(c) !== primary(subs.original));
  const original = subs.original ? t("originalLabel", langNames(subs.original).native) : t("original");
  const shown = langChips($("tx-lang"), "tx-lang", [["", original]], others);
  const mineOriginal = subs.original && myLangIndex(subs.original) >= 0;
  note("tx-note", !subs.languages.length
    ? (state.backend?.asr ? t("noteNoSubsAsr") : t("noteNoSubsNoAsr"))
    : !shown && !mineOriginal ? t("noteNoSubsMine") : "", !subs.languages.length && !state.backend?.asr);

  const aiOn = aiReady();
  $("ai-actions").hidden = !aiOn;
  $("ai-setup").hidden = aiOn;
  $("ai-translate").hidden = Boolean(subs.original) && primary(subs.original) === state.mainLang;
  $("ai-translate-label").textContent = t("aiTranslate", langNames(state.mainLang).native);
  updateVideoNote();
  updateAudioNote();
}

function updateVideoNote() {
  const q = qualityOf();
  const container = radio("container");
  const recode = container === "mp4" && q && !(q.codecs || []).includes("H.264");
  note("video-note", recode ? t("noteRecode") : "", true);
  $("go-video").textContent = !q ? t("download") : container === "auto"
    ? t("downloadQuality", resolutionName(q)) : t("downloadQualityIn", resolutionName(q), container.toUpperCase());
}

function updateAudioNote() {
  const fmt = radio("audio-format");
  const aac = (state.info?.audio_codecs || []).includes("AAC");
  note("audio-note", fmt === "mp3" || (fmt === "m4a" && !aac) ? t("noteAudioConvert") : "");
  $("go-audio").textContent = fmt === "audio" ? t("downloadAudioOriginal") : t("downloadAudioAs", fmt.toUpperCase());
}

function selectTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) {
    b.setAttribute("aria-selected", String(b.dataset.tab === name));
    b.tabIndex = b.dataset.tab === name ? 0 : -1;
  }
  for (const p of document.querySelectorAll(".pane")) p.hidden = p.dataset.pane !== name;
  for (const f of $("video-footer").querySelectorAll("[data-for]")) f.hidden = f.dataset.for !== name;
}

async function startJob(request) {
  try {
    const title = state.info?.title || state.current.title;
    const job = await send({type: "start", request: {...request, ...state.current.target}, title});
    if (job && (request.action === "download" || request.mode !== "copy")) showView("jobs");
  } catch (error) { showStatus(error.message); }
}

function videoRequest() {
  const container = radio("container");
  const index = Number(radio("quality")) || 0;
  const q = qualityOf();
  const audioLang = radio("audio-lang") || "";
  const embed = $("embed-subs").checked ? {embed_subs: true} : {};
  if ((index === 0 && !audioLang) || !q?.height) {
    return {action: "download", preset: {auto: "best", mp4: "mp4", mkv: "mkv"}[container], ...embed};
  }
  return {action: "download", preset: "custom", height: q.height, fps60: q.fps60, hdr: q.hdr, container,
    codec: "auto", audio_lang: audioLang, ...embed};
}

const STATE_TEXT = {queued: "stateQueued", running: "stateRunning", done: "stateDone", error: "stateError", cancelled: "stateCancelled"};

function jobItem(job, fromHistory = false) {
  const act = (label, msg) => h("button", {type: "button", class: "small",
    onclick: () => send(msg).catch(() => note("jobs-status", t("jobActionFailed"), true))}, label);
  const running = job.state === "running" || job.state === "queued";
  const actions = [];
  if (!fromHistory && running) actions.push(act(t("actCancel"), {type: "cancel", id: job.id}));
  if (job.state === "error" || job.state === "cancelled") actions.push(act(t("actRetry"), {type: "retry", id: job.id}));
  if (job.state === "done" && job.path && state.prefs.keepHistory) {
    actions.push(act(t("actOpen"), {type: "open", path: job.path}), act(t("actReveal"), {type: "reveal", path: job.path}));
  }
  if (!fromHistory && !running) actions.push(act(t("actDismiss"), {type: "dismiss", id: job.id}));
  const status = running && job.percent != null ? `${Math.round(job.percent)} %` : t(STATE_TEXT[job.state]);
  const msg = job.state === "running" ? job.detail : running || job.state === "done" ? "" : job.message;
  return h("li", {class: "job"},
    /^https:\/\//.test(job.thumb || "") ? h("img", {src: job.thumb, alt: ""}) : null,
    h("div", {class: "body"},
      h("div", {class: "name"}, job.title || job.label),
      h("div", {class: "sub"}, [job.author, job.label].filter(Boolean).join(" · ")),
      running ? progressBar(job.state === "queued" ? null : job.percent, job.title || job.label) : null,
      h("div", {class: "line"}, h("span", {}, msg || ""), h("span", {class: `state ${job.state}`}, status)),
      actions.length ? h("div", {class: "actions"}, actions) : null));
}

function renderJobs() {
  const jobs = [...state.jobs].sort((a, b) => b.created - a.created);
  const running = jobs.filter((j) => ["running", "queued"].includes(j.state));
  const done = jobs.filter((j) => !running.includes(j));
  const ids = new Set(jobs.map((j) => j.created));
  const past = state.history.filter((x) => !ids.has(x.created)).slice(0, 30);
  $("jobs-running").replaceChildren(...running.map((j) => jobItem(j)));
  $("jobs-done").replaceChildren(...done.map((j) => jobItem(j)));
  $("history").replaceChildren(...past.map((j) => jobItem(j, true)));
  $("jobs-active").hidden = !running.length;
  $("jobs-finished").hidden = !done.length;
  $("history-group").hidden = !past.length;
  $("jobs-empty").hidden = Boolean(jobs.length || past.length);
  $("jobs-count").textContent = String(running.length);
  $("jobs-count").hidden = !running.length;
}

async function clearHistory() {
  if (!confirm(t("clearHistoryConfirm"))) return;
  await send({type: "clearHistory"});
  state.history = [];
  renderJobs();
  $("prefs-saved").textContent = t("historyCleared");
}

const aiReady = () => Boolean(state.prefs?.ai?.provider &&
  (state.prefs.ai.key || ["ollama", "custom"].includes(state.prefs.ai.provider)));

async function setPrefs(partial) {
  state.prefs = await send({type: "setPrefs", prefs: partial});
  $("prefs-saved").textContent = t("saved");
}

function fillPrefs(prefs) {
  const f = $("prefs");
  if (![...f.asrModel.options].some((o) => o.value === prefs.asrModel)) f.asrModel.append(option(prefs.asrModel, prefs.asrModel));
  f.concurrency.value = String(prefs.concurrency);
  if (!f.defaultPreset.options.length) {
    for (const [value, key] of [["best", "menuBest"], ["mp4", "menuMp4"], ["mkv", "menuMkv"],
      ["audio", "menuAudio"], ["m4a", "menuM4a"], ["mp3", "menuMp3"]]) f.defaultPreset.append(option(value, t(key)));
  }
  f.defaultPreset.value = prefs.defaultPreset || "best";
  f.subfolder.value = prefs.subfolder || "";
  f.nameTemplate.value = prefs.nameTemplate || "";
  f.showProgress.checked = prefs.showProgress !== false;
  f.keepHistory.checked = prefs.keepHistory;
  f.session.checked = Boolean(prefs.session);
  f.metrics.checked = Boolean(prefs.metrics);
  f.cacheMb.value = prefs.cacheMb;
  f.asrFallback.checked = prefs.asrFallback;
  f.asrModel.value = prefs.asrModel;
  langPicker(document.querySelector('[data-picker="settings"]'), prefs.langs, (langs) => setPrefs({langs}));
  fillAI(prefs.ai || {});
}

function fillAI(ai) {
  $("ai-provider").value = ai.provider || "";
  $("ai-key").value = ai.key || "";
  $("ai-model").value = ai.model || "";
  $("ai-base").value = ai.base || "";
  renderAI();
}

function renderAI() {
  const provider = $("ai-provider").value;
  $("ai-fields").hidden = !provider;
  $("ai-key-row").hidden = provider === "ollama";
  $("ai-base-row").hidden = !["ollama", "custom"].includes(provider);
  $("ai-model").placeholder = AI_MODELS[provider] || "";
  $("ai-base").placeholder = provider === "ollama" ? "http://localhost:11434/v1" : "https://…/v1";
}

const aiValues = () => ({provider: $("ai-provider").value, key: $("ai-key").value.trim(),
  model: $("ai-model").value.trim(), base: $("ai-base").value.trim()});

async function saveAI() {
  renderAI();
  $("ai-status").textContent = "";
  if (!$("ai-provider").value) browser.permissions.remove({data_collection: ["websiteContent"]}).catch(() => {});
  if ($("ai-provider").value &&
    !await browser.permissions.request({data_collection: ["websiteContent"]}).catch(() => false)) {
    $("ai-status").textContent = t("aiConsentDenied");
    return;
  }
  await setPrefs({ai: aiValues()});
}

async function testAI() {
  $("ai-status").textContent = t("aiTesting");
  try {
    const r = await send({type: "aiTest", ai: aiValues()});
    $("ai-status").textContent = r.message;
  } catch (err) {
    $("ai-status").textContent = err.message;
  }
}

const SESSION_PERMS = {permissions: ["cookies"], origins: ["<all_urls>"]};

async function savePrefs(event) {
  const target = event?.target;
  if (!target || target.type === "search" || target.classList.contains("ui-lang") ||
    target.closest("#ai-card")) return;
  const f = $("prefs");
  if (target === f.session) {
    if (f.session.checked) f.session.checked = await browser.permissions.request(SESSION_PERMS).catch(() => false);
    else browser.permissions.remove(SESSION_PERMS).catch(() => {});
  }
  setPrefs({
    session: f.session.checked,
    concurrency: Math.min(4, Math.max(1, Number(f.concurrency.value) || 2)),
    defaultPreset: f.defaultPreset.value,
    subfolder: f.subfolder.value.trim().slice(0, 120),
    nameTemplate: f.nameTemplate.value.trim().slice(0, 120),
    showProgress: f.showProgress.checked,
    keepHistory: f.keepHistory.checked,
    metrics: f.metrics.checked,
    cacheMb: Math.min(5000, Math.max(0, Math.round(Number(f.cacheMb.value) || 0))),
    asrFallback: f.asrFallback.checked,
    asrModel: f.asrModel.value
  });
}

function renderDonate() {
  const url = state.donate.donateUrl;
  $("support-card").hidden = !url;
  $("donate-top").hidden = !url || state.view === "welcome";
  if (url) $("donate").href = url;
}

function fillUiLang() {
  const auto = langNames(primary(browser.i18n.getUILanguage())).native;
  for (const select of document.querySelectorAll(".ui-lang")) {
    select.replaceChildren(option("", t("uiLangAuto", auto), !state.prefs.uiLang),
      ...UI_LOCALES.map((code) => option(code, langNames(code).native, state.prefs.uiLang === code)));
  }
}

async function changeUiLang(event) {
  await setPrefs({uiLang: event.target.value});
  if (state.view === "settings") location.hash = "#settings";
  location.reload();
}

function renderBackend() {
  const b = state.backend;
  const el = $("backend-status");
  $("backend-guide").hidden = !b || Boolean(b.compatible);
  if (!b) { el.textContent = t("backendChecking"); return; }
  if (b.error) { el.textContent = t("backendUnavailable", b.error); return; }
  el.textContent = [b.compatible ? t("setupAllSet") : "", t("backendComponent", b.version) + (b.compatible ? "" : ` ${t("backendMismatch")}`),
    b.ffmpeg ? "" : t("backendNoFfmpeg"), b.asr ? "" : t("backendNoAsr"),
    b.js_runtime || platform.requiresJsRuntime === false ? "" : t("backendNoDeno")].filter(Boolean).join(" · ");
}

function showWelcome(next) {
  const main = state.mainLang;
  let langs = state.prefs.onboarded ? state.prefs.langs : [main, ...(main === "en" ? [] : ["en"])];
  langPicker(document.querySelector('[data-picker="welcome"]'), langs, (list) => { langs = list; }, true);
  $("welcome-done").onclick = async () => {
    await setPrefs({langs, onboarded: true});
    fillPrefs(state.prefs);
    if (location.hash === "#welcome") {
      const tab = await browser.tabs.getCurrent();
      if (tab) return browser.tabs.remove(tab.id);
    }
    showView(next);
  };
  showView("welcome");
}

async function init() {
  const port = browser.runtime.connect({name: "panel"});
  port.onMessage.addListener((m) => {
    if (m.type === "jobs") { state.jobs = m.jobs; renderJobs(); }
    if (m.type === "donate") { state.donate = m.donate; renderDonate(); }
  });

  const s = await send({type: "state"});
  await setUiLang(s.prefs.uiLang);
  applyI18n();
  Object.assign(state, {jobs: s.jobs, history: s.history, prefs: s.prefs, backend: s.backend, pending: s.pending,
    donate: s.donate || {}, mainLang: s.mainLang || primary(I18N.lang)});
  $("limits-note").textContent = t("limitsNote");
  renderJobs();
  renderDonate();
  fillPrefs(s.prefs);
  fillUiLang();
  await renderImageAi();
  if (browser.browserAction.getUserSettings) {
    const settings = await browser.browserAction.getUserSettings().catch(() => ({}));
    if (!settings.isOnToolbar) { $("pin-hint").hidden = false; $("pin-hint").textContent = t("pinChrome"); }
  } else { $("pin-hint").hidden = false; $("pin-hint").textContent = t("pinFirefox"); }

  if (!state.backend || state.backend.error) state.backend = await send({type: "ping"});
  renderBackend();
  if (state.backend.error) $("setup").hidden = false;

  const view = s.pending?.view;
  const first = location.hash === "#settings" || view === "settings" ? "settings" : view === "jobs" ? "jobs" : "video";
  if (!s.prefs.onboarded || location.hash === "#welcome") return showWelcome(first);
  showView(first);
}

$("open-settings").addEventListener("click", () => { state.back = state.view; showView("settings"); });
$("back").addEventListener("click", () => showView(state.back || "video"));
for (const b of $("nav").querySelectorAll("button")) b.addEventListener("click", () => showView(b.dataset.view));
$("prefs").addEventListener("change", savePrefs);
$("prefs").addEventListener("submit", (e) => e.preventDefault());
$("check-backend").addEventListener("click", async () => {
  $("check-backend").disabled = true;
  const wasUnavailable = !state.backend || Boolean(state.backend.error);
  state.backend = null; renderBackend();
  try { state.backend = await send({type: "ping"}); }
  catch (err) { state.backend = {error: err?.message || String(err)}; }
  finally { $("check-backend").disabled = false; }
  renderBackend();
  $("setup").hidden = !state.backend.error;
  if (wasUnavailable && !state.backend.error) {
    state.videoLoaded = false;
    showStatus(t("backendConnected"));
  }
});
$("copy-install").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(INSTALL_COMMAND); $("setup-result").textContent = t("setupCopied"); }
  catch (_) { $("setup-result").textContent = INSTALL_COMMAND; }
});
$("image-ai-save").addEventListener("click", async () => {
  if ($("image-ai-save").disabled) return;
  const key = $("image-ai-key").value.trim();
  const provider = imageAiProvider(key);
  if (!provider) { $("image-ai-status").textContent = t("imageAiInvalid"); return; }
  if (!confirm(t("imageAiConsentPrompt"))) return;
  const revision = ++imageAiRevision;
  $("image-ai-save").disabled = true;
  try {
    const firefox = !!browser.runtime.getManifest().browser_specific_settings?.gecko;
    const allowed = firefox ? await browser.permissions.request({data_collection: ["websiteContent"]}).catch(() => false) : true;
    if (!allowed) return;
    const granted = await browser.permissions.request({origins: [IMAGE_AI_ORIGINS[provider]]}).catch(() => false);
    if (!granted || revision !== imageAiRevision) return;
    const config = await imageAiValidate(key);
    if (revision !== imageAiRevision) return;
    config.quality = $("image-ai-quality").checked;
    config.consent = true;
    await browser.storage.local.set({pictureAi: config});
    $("image-ai-key").value = "";
    await renderImageAi();
  } catch (error) {
    if (revision === imageAiRevision) $("image-ai-status").textContent = t({invalid: "imageAiInvalid", credit: "imageAiCredit", offline: "imageAiOffline"}[error.message] || "imageAiOffline");
  } finally { $("image-ai-save").disabled = false; }
});
$("image-ai-remove").addEventListener("click", async () => {
  if (!confirm(t("imageAiConfirmRemove"))) return;
  ++imageAiRevision;
  $("image-ai-key").value = "";
  const {pictureAi: removed} = await browser.storage.local.get("pictureAi");
  await browser.storage.local.remove("pictureAi");
  const origin = IMAGE_AI_ORIGINS[removed?.provider];
  if (origin) await browser.permissions.remove({origins: [origin]}).catch(() => {});
  await renderImageAi();
});
$("image-ai-quality").addEventListener("change", async () => {
  const {pictureAi: imageAi} = await browser.storage.local.get("pictureAi");
  if (imageAi) await browser.storage.local.set({pictureAi: {...imageAi, quality: $("image-ai-quality").checked}});
});
$("copy-diagnostics").addEventListener("click", async () => {
  const b = state.backend || {};
  const text = [
    `extension ${browser.runtime.getManifest().version}`,
    navigator.userAgent.replace(/^Mozilla\/5\.0 /, ""),
    `component ${b.version || "-"} (protocol ${b.protocol ?? "-"}, compatible ${b.compatible ?? "-"})`,
    `yt-dlp ${b.ytdlp || "-"} · ffmpeg ${!!b.ffmpeg} · js runtime ${!!b.js_runtime} · asr ${!!b.asr}`,
    b.error ? "component error: yes" : "",
  ].filter(Boolean).join("\n");
  await navigator.clipboard.writeText(text);
  $("prefs-saved").textContent = t("diagnosticsCopied");
});
$("report").addEventListener("click", async () => {
  const s = await send({type: "state"}).catch(() => ({})) || {};
  const b = state.backend || s.backend || {};
  const text = tomoReport({extensionVersion: browser.runtime.getManifest().version,
    componentVersion: b.version, ytdlpVersion: b.ytdlp, ffmpeg: b.ffmpeg, asr: b.asr,
    failedCount: [...(s.jobs || []), ...(s.history || [])].filter((j) => j.state === "error").length});
  const copied = await navigator.clipboard.writeText(text).then(() => true, () => false);
  const platform = await (browser.runtime.getPlatformInfo?.() ?? Promise.resolve({})).catch(() => ({}));
  const url = tomoIssueUrl({extensionVersion: browser.runtime.getManifest().version, componentVersion: b.version,
    os: {win: "Windows", mac: "macOS", linux: "Linux"}[platform.os],
    browser: tomoBrowserName(navigator.userAgent, Boolean(navigator.brave))});
  await browser.tabs.create({url});
  $("prefs-saved").textContent = copied ? t("reportCopied") : t("reportOpened");
});
$("update-ytdlp").addEventListener("click", async () => {
  $("update-ytdlp").disabled = true;
  $("prefs-saved").textContent = `${t("updateYtdlp")}…`;
  try { await send({type: "updateYtdlp"}); $("prefs-saved").textContent = ""; showView("jobs"); }
  catch (_) { $("prefs-saved").textContent = t("updateFailed"); }
  finally { $("update-ytdlp").disabled = false; }
});
$("clear-cache").addEventListener("click", () => send({type: "clearCache"})
  .then(() => { $("prefs-saved").textContent = t("cacheCleared"); }, (e) => { $("prefs-saved").textContent = e.message; }));
$("clear-history").addEventListener("click", clearHistory);
$("clear-history-inline").addEventListener("click", clearHistory);
$("clear-finished").addEventListener("click", () => send({type: "clearFinished"}));
$("select-many").addEventListener("click", toggleMulti);
$("batch-go").addEventListener("click", startBatch);
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
for (const list of [$("nav"), document.querySelector(".tabs")]) list.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  const index = tabs.indexOf(document.activeElement);
  if (index < 0) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
    : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next].click();
  tabs[next].focus();
});
$("retry-resolve").addEventListener("click", () => { if (state.current) select(state.current); });
$("quality").addEventListener("change", updateVideoNote);
$("container").addEventListener("change", updateVideoNote);
$("audio-format").addEventListener("change", updateAudioNote);
$("go-video").addEventListener("click", () => startJob(videoRequest()));
$("go-image").addEventListener("click", async () => {
  $("go-image").disabled = true;
  try {
    await send({type: "saveImage", tabId: state.tab.id, url: state.tab.url});
    showStatus(t("imageSaved"));
  } catch (error) { showStatus(error.message); }
  finally { $("go-image").disabled = false; }
});
$("go-image-ai").addEventListener("click", async () => {
  $("go-image-ai").disabled = true;
  try { await send({type: "enhanceImage", tabId: state.tab.id, url: state.tab.url}); showStatus(t("imageSaved")); }
  catch (error) { showStatus(error.message); }
  finally { $("go-image-ai").disabled = false; }
});
$("go-audio").addEventListener("click", () => startJob({action: "download", preset: radio("audio-format"),
  audio_lang: radio("audio-lang2") || ""}));
const tx = (extra) => startJob({action: "transcript", lang: radio("tx-lang") || "", source: "auto",
  timed: $("tx-timed").checked, ...extra});
$("tx-copy").addEventListener("click", () => tx({mode: "copy"}));
for (const b of document.querySelectorAll("[data-export]")) {
  b.addEventListener("click", () => tx({mode: "export", format: b.dataset.export}));
}
$("ai-summary").addEventListener("click", () => tx({mode: "summary"}));
$("ai-translate").addEventListener("click", () => tx({mode: "translate"}));
$("ai-setup").addEventListener("click", () => openSettingsAt("ai-card"));
$("ai-provider").addEventListener("change", () => {
  $("ai-model").value = "";
  saveAI();
});
for (const id of ["ai-key", "ai-model", "ai-base"]) $(id).addEventListener("change", saveAI);
$("ai-test").addEventListener("click", testAI);
for (const select of document.querySelectorAll(".ui-lang")) select.addEventListener("change", changeUiLang);
$("donate-top").addEventListener("click", () => browser.tabs.create({url: state.donate.donateUrl}).then(() => window.close()));

init().catch((err) => showStatus(err.message));
