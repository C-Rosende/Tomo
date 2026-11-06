function maxImageUrls(src, siteVariants = () => []) {
  let u;
  try { u = new URL(src); } catch { return []; }
  if (!/^https?:$/.test(u.protocol)) return [];
  const out = [];
  const add = (x) => { if (x && x !== src && !out.includes(x)) out.push(x); };
  const host = u.hostname;
  const path = u.pathname;

  if (host === "pbs.twimg.com" && path.startsWith("/media/")) {
    const m = path.match(/^(\/media\/[\w-]+)(?:\.(\w+))?(?::\w+)?$/);
    const fmt = u.searchParams.get("format") || (m && m[2]) || "jpg";
    if (m) add(`https://${host}${m[1]}?format=${fmt}&name=orig`);
  } else if (host === "i.pinimg.com") {
    add(`https://${host}${path.replace(/^\/(?:\d+x\d*|\d+x)\//, "/originals/")}`);
  } else if (host === "preview.redd.it" || host === "external-preview.redd.it") {
    if (host === "preview.redd.it") add(`https://i.redd.it${path}`);
    else add(`https://${host}${path}`);
  } else if (host === "upload.wikimedia.org" || host === "thumb.wikimedia.org") {
    const m = path.match(/^(\/wikipedia\/\w+)\/thumb(\/\w\/\w\w\/[^/]+)\/[^/]+$/);
    if (m) add(`https://upload.wikimedia.org${m[1]}${m[2]}`);
  } else if (host === "i.imgur.com") {
    const m = path.match(/^\/(\w{5}|\w{7})[sbtmlh](\.\w+)$/);
    if (m) add(`https://${host}/${m[1]}${m[2]}`);
    const d = path.match(/^\/(\w{7})_d\.\w+$/);
    if (d) add(`https://${host}/${d[1]}.jpg`);
  } else if (/^media\d*\.giphy\.com$/.test(host)) {
    const m = path.match(/^(\/media\/[^/]+\/)(?:\d+w?|fixed_[^/.]+|downsized[^/.]*|preview|giphy-preview)\.(gif|webp|mp4)$/i);
    if (m) add(`${u.origin}${m[1]}giphy.${m[2]}`);
  } else if (host === "images.unsplash.com" || host === "images.pexels.com") {
    const full = new URL(u);
    for (const key of ["w", "h", "fit"]) full.searchParams.delete(key);
    add(full.href);
  } else if (/^(?:\d+\.)?media\.tumblr\.com$/.test(host) || host === "static.tumblr.com") {
    const full = path.replace(/_(?:75sq|100|250|400|500|540|640|1280)(\.(?:jpe?g|png|gif|webp))$/i, "_1280$1")
      .replace(/\/s\d+x\d+\//, "/s2048x3072/");
    if (full !== path) add(`${u.origin}${full}${u.search}`);
  }
  for (const candidate of siteVariants(u)) add(candidate);
  const wp = path.match(/^(.*)-\d{2,4}x\d{2,4}(\.(?:jpe?g|png|webp|gif))$/i);
  if (wp) add(`${u.origin}${wp[1]}${wp[2]}${u.search}`);

  out.push(src);
  return out;
}

const IMAGE_EXT = /\.(jpe?g|png|gif|webp|avif|apng|svg|bmp|tiff?)$/i;

function largestSrcset(srcset, base) {
  const re = /[\s,]*([^\s,]\S*[^\s,]|[^\s,])\s*(?:(\d+(?:\.\d+)?)([wx]))?\s*(?:,|$)/g;
  const best = {w: null, x: null};
  const text = String(srcset).slice(0, 20000);
  for (let m; (m = re.exec(text)) && m[0];) {
    const unit = m[3] === "w" ? "w" : "x";
    const value = m[2] ? Number(m[2]) : 1;
    if (!best[unit] || value > best[unit].value) best[unit] = {url: m[1], value};
  }
  const pick = best.w || best.x;
  try { return pick ? new URL(pick.url, base).href : ""; } catch { return ""; }
}

function privateHost(host) {
  const h = String(host).toLowerCase().replace(/^\[|\]$/g, "");
    if (h === "localhost" || [".localhost", ".local", ".internal", ".lan", ".home.arpa"].some((suffix) => h.endsWith(suffix)) || !h.includes(".") && !h.includes(":")) return true;
  if (h.includes(":")) return h === "::1" || h === "::" || /^f[cd]/.test(h) || /^fe[89ab]/.test(h) || h.startsWith("::ffff:");
  const p = h.split(".").map(Number);
  if (p.length !== 4 || p.some((n) => !(n >= 0 && n <= 255))) return /^\d+$/.test(p[p.length - 1]);
  return p[0] === 10 || p[0] === 127 || p[0] === 0 || p[0] >= 224 ||
    (p[0] === 169 && p[1] === 254) || (p[0] === 100 && p[1] >= 64 && p[1] <= 127) ||
    (p[0] === 172 && p[1] >= 16 && p[1] <= 31) || (p[0] === 192 && p[1] === 168) ||
    (p[0] === 192 && p[1] === 0) || (p[0] === 198 && (p[1] === 18 || p[1] === 19));
}

const MAX_PAGE_CANDIDATES = 6;

function imageCandidates(image, srcUrl) {
  const out = [];
  let fromPageCount = 0;
  const home =(() => { try { return new URL(image?.base).hostname; } catch { return ""; } })();
  const add = (x, fromPage) => {
    let u;
    try { u = new URL(x); } catch { return; }
    if (!/^https?:$/.test(u.protocol) || out.includes(x)) return;
    if (fromPage) {
      if (u.username || u.password || (privateHost(u.hostname) && u.hostname !== home) || fromPageCount >= MAX_PAGE_CANDIDATES) return;
      fromPageCount++;
    }
    out.push(x);
  };
  const abs = (x) => { try { return new URL(x, image?.base).href; } catch { return ""; } };
  const rules = maxImageUrls(srcUrl || image?.src || "", typeof platform === "undefined" ? undefined : platform.imageVariants);
  for (const url of rules.slice(0, -1)) add(url, false);
  if (image) {
    if (image.link) add(abs(image.link), true);
    const sets = (image.srcset || []).slice(0, 10).map((set) => ({set,
      width: Math.max(0, ...[...String(set).matchAll(/(?:^|\s)(\d+)w(?:\s*,|\s*$)/g)].map((m) => Number(m[1])))}));
    sets.sort((a, b) => b.width - a.width);
    for (const {set} of sets) add(largestSrcset(set, image.base), true);
    for (const value of (image.data || []).slice(0, 10)) if (/^[^\s]{0,2000}$/.test(value) && /[/.]/.test(value)) add(abs(value), true);
    if (image.og) add(abs(image.og), true);
  }
  if (rules.length) add(rules.at(-1), false);
  return out;
}

const IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp",
  "image/avif": "avif", "image/apng": "apng", "image/svg+xml": "svg", "image/bmp": "bmp", "image/tiff": "tiff"};

function imageFileName(url, contentType, title = "", pageTitle = "") {
  const mime = String(contentType || "").split(";")[0].trim().toLowerCase();
  let ext = IMAGE_TYPES[mime];
  if (!ext && !mime) {
    try {
      const match = /\.([a-z0-9]{2,5})$/i.exec(new URL(url).pathname);
      const fromUrl = match?.[1].toLowerCase().replace(/^jpeg$/, "jpg").replace(/^tif$/, "tiff");
      if (Object.values(IMAGE_TYPES).includes(fromUrl)) ext = fromUrl;
    } catch (_) {}
  }
  if (mime === "video/mp4" && /^media\d*\.giphy\.com$/.test(new URL(url).hostname)) ext = "mp4";
  if (!ext) return "";
  let base;
  try { base = decodeURIComponent(new URL(url).pathname.split("/").pop() || ""); } catch { base = ""; }
  const generic = (s) => /^(?:image|img|photo|picture|thumbnail|untitled|imagen|foto|bild|image\s*\d+|img[_-]?\d+)$/i.test(s);
  const hashed = (s) => /^(?:[a-f0-9]{12,}|[a-z0-9_-]{24,})$/i.test(s);
  const cleanTitle = (s) => String(s || "").replace(/https?:\/\/\S+|(?<!\w)[#@][\w.-]+/g, " ")
    .replace(/\s*(?:\|\s*[^|]+|-\s*(?:[A-Z][a-z]+[A-Z][a-z]+|TikTok|Twitter|Instagram|Vimeo)|\bon X)\s*$/, "")
    .replace(/\s+/g, " ").trim();
  const social = /(?:tiktok|twitter|\bon X\b)/i.test(pageTitle) || /^(?:pbs\.twimg\.com|[^/]*tiktokcdn\.com)$/.test(new URL(url).hostname);
  const firstPhrase = (s) => social ? String(s || "").split(/(?<=[.!?])\s+/, 1)[0] : s;
  const candidate = cleanTitle(firstPhrase(title)).replace(/\.[\w-]{2,5}$/, "");
  const page = cleanTitle(firstPhrase(pageTitle));
  base = !candidate || generic(candidate) || hashed(candidate) ? (page || base) : candidate;
  base = base.replace(/\.[\w-]{2,5}$/, "");
  if (hashed(base)) base = page || "";
  if (!base) {
    try { base = new URL(url).hostname.split(".").filter((part) => part !== "www")[0] || "Site"; } catch { base = "Site"; }
    base = `${base[0].toUpperCase() + base.slice(1)} ${new Date().toISOString().slice(0, 10)}`;
  }
  base = base.replace(/\.[\w-]{2,5}$/, "");
  base = base.replace(/[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g, "")
    .replace(/:\s+|[\\/|]/g, " - ").replace(/"/g, "'")
    .replace(/[<>?*\x00-\x1f]/g, "").replace(/:/g, " ").replace(/\s+/g, " ").replace(/^[ .-]+|[ .-]+$/g, "");
  if (/^(?:CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)/i.test(base)) base = `_${base}`;
  if (Array.from(base).length > 120) {
    const cut = Array.from(base).slice(0, 120).join("");
    base = cut.includes(" ") ? cut.slice(0, cut.lastIndexOf(" ")) : cut;
  }
  base = base.replace(/[ .-]+$/g, "");
  return `${base}.${ext}`;
}

if (typeof module !== "undefined") module.exports = {maxImageUrls, imageCandidates, imageFileName, largestSrcset, privateHost};
