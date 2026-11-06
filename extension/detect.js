(() => {
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (_) { return ""; } };
  const media = [];
  for (const el of document.querySelectorAll("video, audio")) {
    const src = el.currentSrc || el.src || abs(el.querySelector("source")?.getAttribute("src") || "");
    const rect = el.getBoundingClientRect();
    media.push({
      kind: el.tagName.toLowerCase(),
      src: /^https?:/.test(src) ? src : "",
      stream: src.startsWith("blob:"),
      poster: /^https?:/.test(el.poster || "") ? el.poster : "",
      width: el.videoWidth || 0,
      height: el.videoHeight || 0,
      duration: Number.isFinite(el.duration) ? Math.round(el.duration) : null,
      visible: rect.width > 40 && rect.height > 40,
      label: el.getAttribute("aria-label") || el.getAttribute("title") || ""
    });
  }
  const frames = [...document.querySelectorAll("iframe[src]")]
    .map((f) => abs(f.getAttribute("src")))
    .filter((u) => /^https?:/.test(u));
  const meta = (name) => document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content || "";
  const image = abs(meta("og:image") || meta("twitter:image"));
  const same = (() => {
    try {
      const og = new URL(meta("og:url") || location.href, location.href);
      return og.pathname === location.pathname && og.searchParams.get("v") === new URL(location.href).searchParams.get("v");
    } catch (_) { return false; }
  })();
  const preview = same ? {title: meta("og:title") || meta("twitter:title") || "", image: /^https:/.test(image) ? image : ""} : {};
  return {url: location.href, title: document.title, top: window === window.top,
    contentType: document.contentType, media, frames, preview};
})();
