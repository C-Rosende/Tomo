"use strict";

const HOST = "local.tomo";
const PROTOCOL = 2;

const primary = (code) => String(code).split("-")[0].toLowerCase();
const mainLang = () => primary(I18N.lang);
const nativeName = (code) => {
  try {
    const name = new Intl.DisplayNames([code], {type: "language", languageDisplay: "standard"}).of(code);
    return name.charAt(0).toLocaleUpperCase(code) + name.slice(1);
  } catch (_) { return code; }
};

const DONATE_URL = "https://ko-fi.com/crosende";

const DEFAULTS = {
  concurrency: 2, langs: [], uiLang: "", asrFallback: true, asrModel: "small",
  cacheMb: 200, keepHistory: true, metrics: false, showProgress: true, onboarded: false,
  defaultPreset: "best", session: false, subfolder: "", nameTemplate: "",
  ai: {provider: "", key: "", model: "", base: ""}
};

browser.runtime.onInstalled.addListener(({reason}) => {
  if (reason === "install") browser.tabs.create({url: browser.runtime.getURL("popup.html#welcome")});
});
const RESOLVE_TTL = 10 * 60 * 1000;
const MAX_RESOLVED = 20;
const MAX_FINISHED = 30;
const HISTORY_MAX = 100;

let prefs = {...DEFAULTS};
let aiConsent = false;
const hasAiConsent = () => browser.permissions.contains({data_collection: ["websiteContent"]}).catch(() => false);
const refreshConsent = () => browser.permissions?.contains({data_collection: ["websiteContent"]})
  ?.catch(() => false)
  .then((ok) => {
    aiConsent = ok;
    if (ok) return;
    for (const job of jobs.values()) {
      if (["summary", "translate"].includes(job.request.mode)) finish(job, "error", t("aiConsentDenied"));
    }
    broadcast();
  });
refreshConsent();
browser.permissions?.onAdded?.addListener(refreshConsent);
browser.permissions?.onRemoved?.addListener(refreshConsent);
let history = [];
let backend = null;
let pending = null;
const jobs = new Map();
const resolved = new Map();
const panels = new Set();
let seq = 0;

function normalizeLangs(list) {
  const extra = (Array.isArray(list) ? list : []).filter((c) => typeof c === "string" && primary(c) !== mainLang());
  return [mainLang(), ...new Set(extra)].slice(0, 8);
}

const ready = browser.storage.local.get({prefs: DEFAULTS, history: []}).then(async (s) => {
  prefs = {...DEFAULTS, ...s.prefs, ai: {...DEFAULTS.ai, ...(s.prefs?.ai || {})}};
  await setUiLang(prefs.uiLang);
  prefs.langs = normalizeLangs(prefs.langs);
  history = Array.isArray(s.history) ? s.history : [];
  await buildMenus();
  updateBadge();
});

const isHttp = (url) => {
  try { return ["http:", "https:"].includes(new URL(url).protocol); }
  catch (_) { return false; }
};

function notify(title, message, id) {
  browser.notifications.create(id || "", {
    type: "basic", iconUrl: browser.runtime.getURL("icon96.png"), title,
    message: String(message || "").slice(0, 400)
  }).catch(console.error);
}

function publicJob(job) {
  const {port, ...rest} = job;
  return rest;
}

const aiReady = () => Boolean(aiConsent && prefs.ai?.provider && (prefs.ai.key || ["ollama", "custom"].includes(prefs.ai.provider)));

let badgeShown = "";
function updateBadge() {
  const active = [...jobs.values()].filter((j) => j.state === "running" || j.state === "queued");
  const known = active.filter((j) => j.percent != null);
  const text = !active.length ? "" : known.length
    ? `${Math.floor(known.reduce((sum, j) => sum + j.percent, 0) / known.length)}%` : "…";
  const title = active.length
    ? t("badgeActive", active.length, active[0].label) + (active[0].detail ? ` · ${active[0].detail}` : "")
    : t("actionTitle");
  if (text + "\n" + title === badgeShown) return;
  badgeShown = text + "\n" + title;
  browser.browserAction.setBadgeText({text});
  browser.browserAction.setTitle({title});
}
browser.browserAction.setBadgeBackgroundColor({color: "#0061e0"});

const BROADCAST_MS = 250;
let broadcastCooling = false;
let broadcastDirty = false;

function broadcast() {
  if (broadcastCooling) { broadcastDirty = true; return; }
  broadcastCooling = true;
  setTimeout(() => {
    broadcastCooling = false;
    if (broadcastDirty) { broadcastDirty = false; broadcast(); }
  }, BROADCAST_MS);
  updateBadge();
  if (!panels.size) return;
  const list = [...jobs.values()].map(publicJob);
  for (const port of panels) {
    try { port.postMessage({type: "jobs", jobs: list}); } catch (_) { panels.delete(port); }
  }
}

const cookieLine = (c) => [(c.httpOnly ? "#HttpOnly_" : "") + c.domain, String(!c.hostOnly).toUpperCase(), c.path,
  String(c.secure).toUpperCase(), Math.floor(c.expirationDate || 0), c.name, c.value].join("\t");

async function storeOf(tab) {
  try {
    if (!tab) [tab] = await browser.tabs.query({active: true, currentWindow: true});
    if (tab?.cookieStoreId) return tab.cookieStoreId;
    const stores = await browser.cookies.getAllCookieStores();
    return stores.find((s) => s.tabIds?.includes(tab?.id))?.id || "";
  } catch (_) {
    return "";
  }
}

async function sessionCookies(request, storeId) {
  if (!prefs.session || !browser.cookies || !storeId) return "";
  const urls = [...new Set([request.url, request.page_url, request.src_url].filter((u) => /^https?:/.test(u || "")))];
  try {
    const seen = new Map();
    for (const url of urls) {
      for (const c of await browser.cookies.getAll({url, storeId})) {
        const key = [c.domain, c.path, c.name].join("|");
        if (!seen.has(key)) seen.set(key, cookieLine(c));
      }
    }
    return [...seen.values()].join("\n");
  } catch (_) {
    return "";
  }
}

async function withSession({store, ...request}) {
  const cookies = await sessionCookies(request, store);
  return cookies ? {...request, cookies} : request;
}

async function nativeCall(request, onProgress) {
  request = await withSession(request);
  return new Promise((resolve, reject) => {
    const port = browser.runtime.connectNative(HOST);
    let finished = false;
    port.onMessage.addListener((m) => {
      if (m.status === "progress") { onProgress?.(m); return; }
      if (m.status === "started") return;
      finished = true;
      port.disconnect();
      if (m.status === "done") resolve(m);
      else reject(new Error(m.message || t("localError")));
    });
    port.onDisconnect.addListener((p) => {
      if (!finished) reject(new Error(p.error?.message || t("componentStartFailed")));
    });
    try { port.postMessage({...request, ui_lang: I18N.lang}); }
    catch (err) {
      try { port.disconnect(); } catch (_) {}
      reject(err);
    }
  });
}

async function ping() {
  try {
    backend = await nativeCall({action: "ping"});
    backend.compatible = backend.protocol === PROTOCOL;
  } catch (err) {
    backend = {error: err.message};
  }
  return backend;
}

function resolve(target, fresh = false) {
  if (platform.blocks(target.url) || platform.blocks(target.src_url)) return Promise.reject(new Error(t("siteUnavailable")));
  const key = target.url + "|" + (target.src_url || "") + "|" + (target.store || "");
  const hit = resolved.get(key);
  if (!fresh && hit?.promise) return hit.promise;
  if (!fresh && hit && Date.now() - hit.at < RESOLVE_TTL) return Promise.resolve(hit.data);
  const entry = {};
  const promise = nativeCall({action: "resolve", ...target, fresh, cache_mb: prefs.cacheMb}).then((data) => {
    if (resolved.get(key) === entry) resolved.set(key, {at: Date.now(), data});
    return data;
  }, (err) => {
    if (resolved.get(key) === entry) resolved.delete(key);
    throw err;
  });
  entry.promise = promise;
  resolved.delete(key);
  resolved.set(key, entry);
  while (resolved.size > MAX_RESOLVED) resolved.delete(resolved.keys().next().value);
  return promise;
}

const donateState = () => ({donateUrl: DONATE_URL});

const LABELS = {
  best: "labelBest", mp4: "labelMp4", "mp4-remux": "labelMp4Remux", mkv: "labelMkv", webm: "labelWebm",
  audio: "labelAudio", mp3: "labelMp3", m4a: "labelM4a", custom: "labelCustom"
};

function describe(request) {
  if (request.action === "download") return LABELS[request.preset] ? t(LABELS[request.preset]) : t("labelDownload");
  if (request.action === "transcript") {
    if (request.mode === "summary") return t("labelSummary");
    if (request.mode === "translate") return t("labelTranslate", request.target_name || request.target || "");
    if (request.mode === "export") return t("labelTranscriptFile", String(request.format || "txt").toUpperCase());
    return request.timed ? t("labelTranscriptTimed") : t("labelTranscript");
  }
  if (request.action === "update-ytdlp") return t("labelUpdate");
  return request.action;
}

function keyOf(request) {
  const {cache_mb, metrics, t_click, queued_ms, langs, asr_fallback, model, page_url, ...rest} = request;
  return JSON.stringify(Object.keys(rest).sort().map((k) => [k, rest[k]]));
}

function enqueue(request, title) {
  if (platform.blocks(request.url) || platform.blocks(request.src_url)) {
    notify(t("notifyFailed"), t("siteUnavailable"));
    return null;
  }
  const ai = request.action === "transcript" && ["summary", "translate"].includes(request.mode);
  if (ai && !aiReady()) {
    notify(t("notifyFailed"), t("notifyAiMissing"));
    return null;
  }
  const key = keyOf(request);
  for (const job of jobs.values()) {
    if (job.key === key && (job.state === "queued" || job.state === "running")) {
      notify(t("notifyAlreadyRunning"), `${job.label}: ${job.title || ""}`);
      return job;
    }
  }
  if (request.action === "transcript" || request.embed_subs) {
    Object.assign(request, {langs: prefs.langs, asr_fallback: prefs.asrFallback, model: prefs.asrModel});
  }
  if (ai) Object.assign(request, {target: request.target || mainLang(), target_name: request.target_name || nativeName(mainLang())});
  request.cache_mb = prefs.cacheMb;
  if (prefs.subfolder) request.subfolder = prefs.subfolder;
  if (request.action === "download" && prefs.nameTemplate) request.name_template = prefs.nameTemplate;
  if (!prefs.keepHistory) request.record = false;
  request.metrics = prefs.metrics;
  request.t_click = Date.now();
  const job = {
    id: ++seq, key, request, label: describe(request), title: title || "", state: "queued",
    stage: "", percent: null, detail: t("stateQueued"), message: "", path: null, created: Date.now()
  };
  jobs.set(job.id, job);
  const finished = [...jobs].filter(([, j]) => !["queued", "running"].includes(j.state));
  for (const [id] of finished.slice(0, Math.max(0, finished.length - MAX_FINISHED))) jobs.delete(id);
  pump();
  broadcast();
  return job;
}

function pump() {
  const running = [...jobs.values()].filter((j) => j.state === "running").length;
  const queued = [...jobs.values()].filter((j) => j.state === "queued");
  for (const job of queued.slice(0, Math.max(0, prefs.concurrency - running))) start(job);
}

function start(job) {
  job.state = "running";
  job.started = Date.now();
  job.detail = t("starting");
  job.request.queued_ms = job.started - job.created;
  let port;
  try { port = browser.runtime.connectNative(HOST); }
  catch (err) {
    finish(job, "error", err?.message || t("componentStartFailed"));
    broadcast();
    return;
  }
  job.port = port;
  port.onMessage.addListener((m) => {
    if (m.status === "started") {
      if (m.protocol !== PROTOCOL) job.detail = t("protocolMismatch");
    } else if (m.status === "progress") {
      Object.assign(job, {stage: m.stage, percent: m.percent, detail: m.detail});
      if (m.title) job.title = m.title;
      if (m.author) job.author = m.author;
      if (m.thumbnail) job.thumb = m.thumbnail;
    } else if (m.status === "done") {
      finish(job, "done", m.message, {path: m.path || null, title: m.title || job.title});
    } else if (m.status === "error") {
      job.errorKind = m.kind;
      finish(job, "error", m.message);
    } else if (m.status === "cancelled") {
      finish(job, "cancelled", t("cancelledMsg"));
    }
    broadcast();
  });
  port.onDisconnect.addListener((p) => {
    if (job.state === "running") {
      const missing = /No such native application|not found/i.test(p.error?.message || "");
      finish(job, "error", missing ? t("componentMissing") : p.error?.message || t("componentClosed"));
      broadcast();
    }
  });
  const ai = job.request.mode === "summary" || job.request.mode === "translate" ? {ai: prefs.ai} : {};
  const consent = ai.ai ? hasAiConsent() : Promise.resolve(true);
  Promise.all([withSession(job.request), consent]).then(([request, ok]) => {
    if (job.state !== "running") return;
    if (!ok) {
      finish(job, "error", t("aiConsentDenied"));
      broadcast();
      return;
    }
    port.postMessage({...request, ...ai, ui_lang: I18N.lang});
  }).catch((err) => {
    finish(job, "error", err?.message || t("componentStartFailed"));
    broadcast();
  });
}

function finish(job, state, message, extra = {}) {
  if (job.state !== "running" && job.state !== "queued") return;
  Object.assign(job, {state, message, ended: Date.now(), percent: state === "done" ? 100 : job.percent}, extra);
  if (job.port) {
    try { job.port.disconnect(); } catch (_) {}
    job.port = null;
  }
  if (state === "done") {
    const title = job.request.action === "transcript" && job.request.mode === "copy"
      ? t("notifyTranscriptReady") : t("notifyDone");
    notify(title, message, job.path ? `job-${job.id}` : "");
  } else if (state === "error" && canHeal(job)) {
    heal(job);
    pump();
    return;
  } else if (state === "error") {
    notify(t("notifyFailed"), `${job.label}: ${message}`);
  }
  remember(job);
  pump();
}

const UPDATE_EVERY = 3 * 24 * 3600 * 1000;
let lastUpdateTry = 0;

async function updateYtdlp(urgent = false) {
  const {lastYtdlpUpdate = 0} = await browser.storage.local.get("lastYtdlpUpdate");
  if (!urgent && Date.now() - lastYtdlpUpdate < UPDATE_EVERY) return false;
  if (Date.now() - lastUpdateTry < 3600 * 1000) return false;
  lastUpdateTry = Date.now();
  await nativeCall({action: "update-ytdlp"});
  await browser.storage.local.set({lastYtdlpUpdate: Date.now()});
  resolved.clear();
  return true;
}

function scheduledUpdate() {
  const busy = [...jobs.values()].some((j) => j.state === "running" || j.state === "queued");
  if (!busy && !backend?.error) updateYtdlp().catch(() => {});
}
platform.every(scheduledUpdate, 2 * 60 * 1000, 6 * 3600 * 1000);

const canHeal = (job) => job.errorKind === "extractor" && !job.request.healed &&
  ["download", "transcript"].includes(job.request.action);

async function heal(job) {
  job.message = t("healing");
  broadcast();
  await updateYtdlp(true).catch(() => {});
  const {cache_mb, langs, asr_fallback, model, metrics, t_click, queued_ms, ...request} = job.request;
  jobs.delete(job.id);
  enqueue({...request, healed: true}, job.title);
}

function cancel(id) {
  const job = jobs.get(id);
  if (!job) return;
  if (job.state === "queued") {
    finish(job, "cancelled", t("cancelledMsg"));
  } else if (job.state === "running" && job.port) {
    job.detail = t("cancelling");
    try { job.port.postMessage({cmd: "cancel"}); } catch (_) {}
    const port = job.port;
    setTimeout(() => {
      if (job.state === "running" && job.port === port) finish(job, "cancelled", t("cancelledMsg"));
    }, 10000);
  }
  broadcast();
}

function retry(id) {
  const job = jobs.get(id) || history.find((h) => h.id === id);
  if (!job) return null;
  const {cache_mb, langs, asr_fallback, model, metrics, t_click, queued_ms, ...request} = job.request;
  return enqueue(request, job.title);
}

function remember(job) {
  if (!prefs.keepHistory || job.request.action === "update-ytdlp") return;
  const {id, label, title, author, thumb, state, message, path, created, ended, request} = job;
  history = [{id: `h${created}-${id}`, label, title, author, thumb, state, message, path, created, ended, request},
    ...history].slice(0, HISTORY_MAX);
  browser.storage.local.set({history});
}

function enqueueBatch(items, request) {
  return items.slice(0, 200).map((item) => enqueue({...request, ...item.target}, item.title)).filter(Boolean).length;
}

const CONTEXTS = ["video", "audio", "page", "link", "frame"];
const MENU = [
  ["root", null, "menuRoot"],
  ["dl-default", "root", "menuDefault"],
  ["dl-default-sep", "root", null],
  ["dl", "root", "menuVideo"],
  ["dl-best", "dl", "menuBest"],
  ["dl-mp4", "dl", "menuMp4"],
  ["dl-mkv", "dl", "menuMkv"],
  ["dl-sep", "dl", null],
  ["dl-pick", "dl", "menuPickQuality"],
  ["au", "root", "menuAudio"],
  ["au-orig", "au", "menuAudioOriginal"],
  ["au-m4a", "au", "menuM4a"],
  ["au-mp3", "au", "menuMp3"],
  ["tx", "root", "menuText"],
  ["tx-copy", "tx", "menuCopy"],
  ["tx-timed", "tx", "menuCopyTimed"],
  ["tx-sep", "tx", null],
  ["tx-txt", "tx", "menuSaveTxt"],
  ["tx-srt", "tx", "menuSaveSrt"],
  ["tx-sep2", "tx", null],
  ["tx-sum", "tx", "menuSummarize"],
  ["tx-lang", "tx", "menuPickLang"],
  ["root-sep", "root", null],
  ["more-panel", "root", "menuPanel"],
  ["more-folder", "root", "menuFolder"],
  ["img-save", null, "menuImage"],
  ["img-save-enhanced", "img-save", "imageAiDownload"],
  ["img-bg", null, "menuImage"],
  ...["save", "bg"].flatMap((kind) => [
    [`img-${kind}-original`, `img-${kind}`, "imageOriginal"],
  ]),
];

const buildMenus = () => browser.menus.removeAll().then(() => {
  for (const [id, parentId, key] of MENU) {
    const opts = {id, contexts: id.startsWith("img-save") ? ["image"] : id.startsWith("img-bg") ? ["page", "link"] : CONTEXTS};
    if (id === "img-bg" || id === "img-save-enhanced") opts.visible = false;
    if (parentId) opts.parentId = parentId;
    if (key) opts.title = t(key); else opts.type = "separator";
    browser.menus.create(opts);
  }
  updateMenus();
  platform.watchMenus?.();
});

let bgMenu = 0;
browser.menus.onShown?.addListener(async (info, tab) => {
  const mine = ++bgMenu;
  let show = false;
  if (info.targetElementId != null && tab?.id && !info.contexts.some((c) => ["image", "video", "audio", "editable", "selection"].includes(c))) {
    const found = await platform.probe(info, tab).catch(() => null);
    show = !!(found?.bg || found?.image);
  }
  if (mine !== bgMenu) return;
  await browser.menus.update("img-bg", {visible: show}).catch(() => {});
  if (show && mine === bgMenu) browser.menus.refresh();
});
browser.menus.onHidden?.addListener(() => { bgMenu++; browser.menus.update("img-bg", {visible: false}).catch(() => {}); });

const PRESET_ID = {best: "dl-best", mp4: "dl-mp4", mkv: "dl-mkv", audio: "au-orig", m4a: "au-m4a", mp3: "au-mp3"};
const PRESET_LABEL = {best: "menuBest", mp4: "menuMp4", mkv: "menuMkv", audio: "menuAudio", m4a: "menuM4a", mp3: "menuMp3"};

const validPreset = (value) => typeof value === "string" && Object.hasOwn(PRESET_ID, value);
const quickPreset = () => (validPreset(prefs.defaultPreset) ? prefs.defaultPreset : DEFAULTS.defaultPreset);

function updateMenus() {
  browser.menus.update("tx-sum", {visible: aiReady()}).catch(() => {});
  browser.storage.local.get("pictureAi").then(({pictureAi}) =>
    browser.menus.update("img-save-enhanced", {visible: !!(pictureAi?.key && imageAiProvider(pictureAi.key) === pictureAi.provider)}).catch(() => {}));
  const preset = quickPreset();
  browser.menus.update("dl-default", {title: `${t("menuDefault")}: ${t(PRESET_LABEL[preset])}`}).catch(() => {});
}
browser.storage.onChanged?.addListener((changes, area) => {
  if (area === "local" && changes.pictureAi) updateMenus();
});

const ACTIONS = {
  "dl-best": {action: "download", preset: "best"},
  "dl-mp4": {action: "download", preset: "mp4"},
  "dl-mkv": {action: "download", preset: "mkv"},
  "au-orig": {action: "download", preset: "audio"},
  "au-m4a": {action: "download", preset: "m4a"},
  "au-mp3": {action: "download", preset: "mp3"},
  "tx-copy": {action: "transcript", mode: "copy"},
  "tx-timed": {action: "transcript", mode: "copy", timed: true},
  "tx-txt": {action: "transcript", mode: "export", format: "txt"},
  "tx-srt": {action: "transcript", mode: "export", format: "srt"},
  "tx-sum": {action: "transcript", mode: "summary"},
};
const PANEL_VIEWS = {"dl-pick": "video", "tx-lang": "transcript", "more-panel": "video"};

function targetOf(info, tab) {
  const top = info.pageUrl || tab?.url || "";
  const frame = isHttp(info.frameUrl) ? info.frameUrl : "";
  const page = frame || top;
  const src = isHttp(info.srcUrl) ? info.srcUrl : "";
  if (isHttp(info.linkUrl) && !src) return {url: info.linkUrl, page_url: isHttp(page) ? page : "", src_url: ""};
  if (!isHttp(page)) return src ? {url: src, page_url: "", src_url: ""} : null;
  return {url: page, page_url: frame && isHttp(top) ? top : "", src_url: src && src !== page ? src : ""};
}

const bare = (u) => { try { const x = new URL(u); x.hash = ""; return x.href; } catch (_) { return u; } };

async function refineTarget(info, tab, target) {
  if (!platform.canProbe(info, tab)) return target;
  try {
    const found = await platform.probe(info, tab);
    if (!found) return target;
    if (isHttp(found.link) && bare(found.link) !== bare(target.url)) {
      return {url: found.link, page_url: target.url, src_url: isHttp(found.src) ? found.src : target.src_url};
    }
    if (isHttp(found.src) && !target.src_url && found.src !== target.url) return {...target, src_url: found.src};
  } catch (_) {
  }
  return target;
}

async function headType(url) {
  try {
    const r = await fetch(url, {method: "HEAD", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-store", signal: AbortSignal.timeout(30000)});
    if (r.ok && r.headers.get("content-type")) return r.headers.get("content-type");
  } catch (_) {}
  try {
    const r = await fetch(url, {headers: {Range: "bytes=0-0"}, credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-store", signal: AbortSignal.timeout(30000)});
    const type = r.ok ? r.headers.get("content-type") || "" : null;
    await r.body?.cancel().catch(() => {});
    return type;
  } catch (_) { return null; }
}

function assertImageDimensions(bytes) {
  const size = bytes.length;
  const view = new DataView(bytes.buffer, bytes.byteOffset, size);
  let width = 0, height = 0;
  if (bytes[0] === 0x89 && bytes[1] === 0x50 && size >= 24) {
    width = view.getUint32(16); height = view.getUint32(20);
  } else if (bytes[0] === 0x47 && bytes[1] === 0x49 && size >= 10) {
    width = view.getUint16(6, true); height = view.getUint16(8, true);
  } else if (bytes[0] === 0xff && bytes[1] === 0xd8) {
    for (let p = 2; p + 9 < size;) {
      if (bytes[p] !== 0xff) break;
      const marker = bytes[p + 1], length = view.getUint16(p + 2);
      if (length < 2) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
        height = view.getUint16(p + 5); width = view.getUint16(p + 7); break;
      }
      p += length + 2;
    }
  } else if (size >= 30 && String.fromCharCode(...bytes.subarray(0, 4)) === "RIFF"
      && String.fromCharCode(...bytes.subarray(8, 12)) === "WEBP") {
    const variant = String.fromCharCode(...bytes.subarray(12, 16));
    if (variant === "VP8X") {
      width = view.getUint8(24) | (view.getUint8(25) << 8) | (view.getUint8(26) << 16);
      height = view.getUint8(27) | (view.getUint8(28) << 8) | (view.getUint8(29) << 16);
      width++; height++;
    } else if (variant === "VP8L") {
      const bits = view.getUint32(21, true);
      width = (bits & 0x3fff) + 1; height = ((bits >>> 14) & 0x3fff) + 1;
    } else if (variant === "VP8 ") {
      width = view.getUint16(26, true) & 0x3fff; height = view.getUint16(28, true) & 0x3fff;
    }
  }
  if (!width || !height || width > 16384 || height > 16384 || width * height > 40_000_000) throw new Error(t("notifyImageUnsupported"));
}

const inSubfolder = (name) => [...String(prefs.subfolder || "").split(/[\\/]+/)
  .map((p) => p.replace(/[<>:"|?*\x00-\x1f]/g, "_").replace(/[. ]+$/, "").trim()).filter(Boolean).slice(0, 3), name].join("/");

async function browserImageFallback(url, filename) {
  const response = await fetch(url, {credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-store", signal: AbortSignal.timeout(30000)});
  if (!response.ok || !/^image\/(png|jpeg|gif|webp)/i.test(response.headers.get("content-type") || "")) throw new Error(t("notifyImageUnsupported"));
  if (Number(response.headers.get("content-length")) > 2 * 1024 * 1024) throw new Error(t("notifyImageUnsupported"));
  const chunks = [];
  let size = 0;
  const reader = response.body.getReader();
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 2 * 1024 * 1024) throw new Error(t("notifyImageUnsupported"));
      chunks.push(value);
    }
  } finally { reader.cancel().catch(() => {}); }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const part of chunks) { bytes.set(part, at); at += part.length; }
  assertImageDimensions(bytes);
  const binary = Array.from(bytes, (b) => String.fromCharCode(b)).join("");
  const data = `data:${response.headers.get("content-type")};base64,${btoa(binary)}`;
  const id = await browser.downloads.download({url: data, filename: inSubfolder(filename), conflictAction: "uniquify"});
  if (!await downloadFinished(id)) throw new Error(t("notifyImageUnsupported"));
  return data;
}

async function enhancedImage(url, tab) {
  const parsed = new URL(url);
  if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password || privateHost(parsed.hostname) || platform.blocks(url)) throw new Error(t("notifyImageUnsupported"));
  const {pictureAi: imageAi} = await browser.storage.local.get("pictureAi");
  if (!imageAi || imageAiProvider(imageAi.key) !== imageAi.provider) throw new Error(t("imageAiInvalid"));
  if (!imageAi.consent || (browser.runtime.getManifest().browser_specific_settings?.gecko && !await hasAiConsent())) {
    throw new Error(t("imageAiConsentPrompt"));
  }
  await saveImage(url, {srcUrl: url, pageUrl: tab?.url || url}, tab);
  const response = await fetch(url, {credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-store", signal: AbortSignal.timeout(30000)});
  if (!response.ok || !/^image\/(png|jpeg|webp)(?:;|$)/i.test(response.headers.get("content-type") || "")) throw new Error(t("notifyImageUnsupported"));
  const reader = response.body.getReader();
  let size = 0; const parts = [];
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 2 * 1024 * 1024) throw new Error(t("notifyImageUnsupported"));
      parts.push(value);
    }
  } finally { reader.cancel().catch(() => {}); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.length; }
  assertImageDimensions(bytes);
  const binary = Array.from(bytes, (b) => String.fromCharCode(b)).join("");
  const data = `data:${response.headers.get("content-type").split(";", 1)[0]};base64,${btoa(binary)}`;
  let result;
  try { result = await imageAiGenerate(imageAi, data, imageAi.quality); }
  catch (error) { throw new Error(t({invalid: "imageAiInvalid", credit: "imageAiCredit", offline: "imageAiOffline"}[error.message] || "imageAiOffline")); }
  const name = (imageFileName(url, "image/png", "", tab?.title) || "Image.png").replace(/\.[^.]+$/, "_enhanced.png");
  if (result.url) {
    const output = new URL(result.url);
    if (output.protocol !== "https:" || privateHost(output.hostname)) throw new Error(t("notifyImageUnsupported"));
    const helper = backend?.compatible ? backend : await ping();
    if (helper?.compatible) await nativeCall({action: "save-image", url: output.href, filename: name, subfolder: prefs.subfolder});
    else await browserImageFallback(output.href, name);
  } else {
    if (result.data.length > 25 * 1024 * 1024) throw new Error(t("notifyImageUnsupported"));
    const id = await browser.downloads.download({url: result.data, filename: inSubfolder(name), conflictAction: "uniquify"});
    if (!await downloadFinished(id)) throw new Error(t("notifyImageUnsupported"));
  }
}

async function saveImage(src, info, tab) {
  const found = platform.canProbe(info, tab) ? await platform.probe(info, tab).catch(() => null) : null;
  const foreign = (u, base) => {
    try { const host = new URL(u).hostname; return privateHost(host) && host !== new URL(base).hostname; } catch (_) { return true; }
  };
  let bg = found?.bg;
  if (bg && foreign(bg.url, bg.base)) bg = null;
  let image = found?.image || (bg ? {base: bg.base, src: bg.url, srcset: [], link: "", data: []} : null);
  if (!src && image && foreign(image.src, image.base)) image = null;
  src = src || image?.src || "";
  const urls = imageCandidates(image, src);
  if (!urls.length) throw new Error(t("notifyImageUnsupported"));
  const candidates = urls.length > 1 && urls[0] !== src ? urls.filter((url) => url !== src) : urls;
  let helperWasAvailable = false;
  for (const url of candidates) {
    let parsed;
    try { parsed = new URL(url); } catch (_) { continue; }
    if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password || privateHost(parsed.hostname) || platform.blocks(url)) continue;
    const helper = backend?.compatible ? backend : await ping();
    if (helper?.compatible) helperWasAvailable = true;
    if (helperWasAvailable) {
      if (!helper?.compatible) continue;
      const filename = imageBaseName(url, image?.title, tab?.title);
      try { await nativeCall({action: "save-image", url, filename, subfolder: prefs.subfolder}); return; } catch (_) { continue; }
    }
    const type = await headType(url);
    const giphyMp4 = /^https:\/\/media\d*\.giphy\.com\/media\/[^/]+\/giphy\.mp4(?:\?|$)/.test(url);
    if (type && !String(type).toLowerCase().startsWith("image/") && !(giphyMp4 && String(type).toLowerCase().startsWith("video/mp4"))) continue;
    if (!type && url !== src) continue;
    const filename = imageFileName(url, type || "", image?.title, tab?.title);
    try { await browserImageFallback(url, filename); return; } catch (_) { continue; }
  }
  throw new Error(t("notifyImageUnsupported"));
}

function downloadFinished(id) {
  return new Promise((done) => {
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      browser.downloads.onChanged.removeListener(onChanged);
      clearTimeout(timer);
      done(ok);
    };
    const onChanged = (d) => {
      if (d.id !== id || !d.state) return;
      if (d.state.current === "complete") finish(true);
      if (d.state.current === "interrupted") finish(false);
    };
    const timer = setTimeout(() => finish(false), 300000);
    browser.downloads.onChanged.addListener(onChanged);
    browser.downloads.search({id}).then(([d]) => {
      if (d?.state === "complete") finish(true);
      else if (d?.state === "interrupted") finish(false);
    }).catch(() => {});
  });
}

browser.menus.onClicked.addListener((info, tab) => {
  if ([info.pageUrl, info.frameUrl, info.srcUrl, info.linkUrl, tab?.url].some(platform.blocks)) {
    notify(t("notifyFailed"), t("siteUnavailable"));
    return;
  }
  const id = String(info.menuItemId);
  if (PANEL_VIEWS[id]) {
    const ctx = {view: PANEL_VIEWS[id], target: targetOf(info, tab), title: tab?.title || "", tabId: tab?.id};
    ctx.ready = ctx.target ? refineTarget(info, tab, ctx.target).then((x) => {
      ctx.target = x;
      resolve(x).catch(() => {});
    }) : Promise.resolve();
    pending = ctx;
    browser.browserAction.openPopup().catch(() => notify(t("notifyPanelUnavailable"), t("notifyPanelUnavailableText")));
    return;
  }
  const imageMenu = /^img-(save|bg)-original$/.exec(id);
  if (imageMenu) {
    saveImage(imageMenu[1] === "save" ? info.srcUrl : "", info, tab)
      .catch((e) => notify(t("notifyImageFailed"), e.message));
    return;
  }
  if (id === "img-save-enhanced") {
    enhancedImage(info.srcUrl || "", tab).catch((e) => notify(t("notifyImageFailed"), e.message));
    return;
  }
  if (id === "more-folder") {
    nativeCall({action: "open-downloads"}).catch((e) => notify(t("notifyFolderFailed"), e.message));
    return;
  }
  const base = ACTIONS[id === "dl-default" ? PRESET_ID[quickPreset()] : id];
  if (!base) return;
  const target = targetOf(info, tab);
  if (!target) {
    notify(t("notifyNoUrl"), t("notifyNoUrlText"));
    return;
  }
  if (prefs.showProgress && (base.action === "download" || base.mode === "summary")) {
    pending = {view: "jobs", ready: Promise.resolve()};
    browser.browserAction.openPopup().catch(() => { pending = null; });
  }
  Promise.all([ready, refineTarget(info, tab, target), storeOf(tab)])
    .then(([, refined, store]) => enqueue({...base, ...refined, store}, tab?.title || ""));
});

browser.notifications.onClicked.addListener((nid) => {
  const id = Number(String(nid).replace("job-", ""));
  const job = jobs.get(id);
  if (job?.path && prefs.keepHistory) nativeCall({action: "reveal", path: job.path})
    .catch((e) => notify(t("notifyFolderFailed"), e.message));
});

const fromOwnPage = (sender) => sender?.id === browser.runtime.id
  && String(sender.url || "").startsWith(browser.runtime.getURL(""));

browser.runtime.onConnect.addListener((port) => {
  if (port.name !== "panel" || !fromOwnPage(port.sender)) return;
  panels.add(port);
  port.onDisconnect.addListener(() => panels.delete(port));
});

const detect = (tabId) => platform.detect(tabId);

browser.runtime.onMessage.addListener(async (msg, sender) => {
  if (!fromOwnPage(sender)) return undefined;
  await ready;
  switch (msg?.type) {
    case "state": {
      const ctx = pending;
      pending = null;
      if (ctx) await ctx.ready;
      const {ready: _, ...plain} = ctx || {};
      return {jobs: [...jobs.values()].map(publicJob), history, prefs, backend, pending: ctx ? plain : null,
        donate: donateState(), mainLang: mainLang(), uiLang: I18N.lang};
    }
    case "ping": return ping();
    case "saveImage": {
      const tab = await browser.tabs.get(msg.tabId);
      if (!tab || tab.url !== msg.url || !isHttp(tab.url) || platform.blocks(tab.url)) throw new Error(t("notifyImageUnsupported"));
      await saveImage(tab.url, {pageUrl: tab.url, srcUrl: tab.url}, tab);
      return true;
    }
    case "enhanceImage": {
      const tab = await browser.tabs.get(msg.tabId);
      if (!tab || tab.url !== msg.url || !isHttp(tab.url) || platform.blocks(tab.url)) throw new Error(t("notifyImageUnsupported"));
      await enhancedImage(tab.url, tab);
      return true;
    }
    case "detect": return detect(msg.tabId);
    case "resolve": return resolve({...msg.target, store: await storeOf()}, Boolean(msg.fresh));
    case "start": {
      const job = enqueue({store: await storeOf(), ...msg.request}, msg.title);
      return job ? publicJob(job) : null;
    }
    case "startBatch": return enqueueBatch(msg.items || [], {store: await storeOf(), ...msg.request});
    case "cancel": cancel(msg.id); return true;
    case "retry": { const job = retry(msg.id); return job ? publicJob(job) : null; }
    case "dismiss": {
      const job = jobs.get(msg.id);
      if (job && !["queued", "running"].includes(job.state)) jobs.delete(msg.id);
      broadcast();
      return true;
    }
    case "clearFinished":
      for (const [id, job] of jobs) if (!["queued", "running"].includes(job.state)) jobs.delete(id);
      broadcast();
      return true;
    case "clearHistory":
      history = [];
      await browser.storage.local.set({history});
      await nativeCall({action: "clear-history"}).catch(() => {});
      return true;
    case "reveal": return nativeCall({action: "reveal", path: msg.path});
    case "open": return nativeCall({action: "open", path: msg.path});
    case "openDownloads": return nativeCall({action: "open-downloads"});
    case "clearCache":
      resolved.clear();
      return nativeCall({action: "clear-cache"});
    case "updateYtdlp": return publicJob(enqueue({action: "update-ytdlp"}, "yt-dlp"));
    case "aiTest":
      if (!await hasAiConsent()) throw new Error(t("aiConsentDenied"));
      return nativeCall({action: "ai-test", ai: msg.ai || prefs.ai});
    case "setPrefs": {
      const lang = prefs.uiLang;
      const oldProvider = prefs.ai?.provider;
      prefs = {...DEFAULTS, ...prefs, ...msg.prefs};
      if (prefs.uiLang !== lang) {
        await setUiLang(prefs.uiLang);
        await buildMenus();
        updateBadge();
      }
      prefs.ai = {...DEFAULTS.ai, ...(prefs.ai || {})};
      prefs.langs = normalizeLangs(prefs.langs);
      prefs.defaultPreset = quickPreset();
      if (!prefs.keepHistory) {
        history = [];
        await browser.storage.local.set({history});
        nativeCall({action: "clear-history"}).catch(() => {});
      }
      await browser.storage.local.set({prefs});
      if (oldProvider !== prefs.ai.provider) await refreshConsent();
      updateMenus();
      pump();
      return prefs;
    }
    default:
      return undefined;
  }
});
