function probeElement(el) {
  if (!el) return null;
  const POST = /\/(status|statuses|posts?|p|reels?|videos?|watch|comments|shorts|clips?|v)\b/i;
  let media = el.closest("video, audio");
  for (let p = el, d = 0; !media && p && p !== document.body && d < 6; p = p.parentElement, d++) {
    const found = p.querySelectorAll ? p.querySelectorAll("video, audio") : [];
    if (found.length === 1) media = found[0];
    if (found.length > 1) break;
  }
  const src = media && /^https?:/.test(media.currentSrc || media.src) ? (media.currentSrc || media.src) : "";
  const isPost = (a) => a.querySelector("time") || POST.test(a.pathname);
  let link = "";
  const article = (media || el).closest("article");
  if (article) {
    const links = [...article.querySelectorAll("a[href]")];
    const a = links.find((x) => x.querySelector("time")) || links.find((x) => POST.test(x.pathname));
    link = a ? a.href : "";
  } else {
    for (let p = media || el, d = 0; p && p !== document.body && d < 10; p = p.parentElement, d++) {
      const c = [...new Set([...(p.querySelectorAll ? p.querySelectorAll("a[href]") : [])].filter(isPost).map((a) => a.href))];
      if (c.length > 1) break;
      if (c.length === 1) { link = c[0]; break; }
    }
  }
  let image = null;
  let img = el.closest("img");
  if (!img && !media && el.nodeType === 1) {
    const box = el.getBoundingClientRect();
    const covers = (i) => {
      const r = i.getBoundingClientRect();
      const w = Math.min(box.right, r.right) - Math.max(box.left, r.left);
      const h = Math.min(box.bottom, r.bottom) - Math.max(box.top, r.top);
      return r.width >= 100 && r.height >= 100 && w > 0 && h > 0 && (w * h) / (r.width * r.height) >= 0.6 &&
        box.width * box.height <= 1.5 * r.width * r.height;
    };
    for (let p = el, d = 0; !img && p && p !== document.body && d < 4; p = p.parentElement, d++) {
      const big = [...p.querySelectorAll("img")].slice(0, 30).filter(covers);
      if (big.length === 1) img = big[0];
      if (big.length > 1) break;
    }
  }
  if (img) {
    const pic = img.closest("picture");
    const applies = (s) => { try { return !s.media || matchMedia(s.media).matches; } catch (_) { return false; } };
    const sources = pic ? [...pic.querySelectorAll("source")].slice(0, 10).filter(applies) : [];
    const names = ["data-src", "data-original", "data-full", "data-fullsize", "data-large", "data-large-file",
      "data-orig-file", "data-zoom-src", "data-hi-res", "data-lazy-src"];
    let fullLink = img.closest("a[href]")?.href || "";
    try {
      const wrapper = new URL(fullLink);
      if (/^(?:[\w-]+\.)*google\.(?:com|[a-z]{2,3}|co\.[a-z]{2})$/.test(wrapper.hostname) && wrapper.pathname === "/imgres")
        fullLink = wrapper.searchParams.get("imgurl") || fullLink;
    } catch (_) {}
    const og = document.querySelectorAll("img").length === 1
      ? document.querySelector('meta[property="og:image"], meta[name="twitter:image"]')?.content || "" : "";
    image = {
      base: document.baseURI,
      title: img.getAttribute("alt") || img.getAttribute("title") || "",
      src: img.currentSrc || img.src,
      srcset: [img, ...sources].map((x) => x.getAttribute("srcset")).filter(Boolean),
      link: fullLink,
      og,
      data: [img, img.parentElement, img.closest("a")].filter(Boolean)
        .flatMap((node) => names.map((n) => node.getAttribute(n)).filter(Boolean)).slice(0, 10),
    };
  }
  let bg = null;
  if (!img && !media) {
    for (let p = el, d = 0; p && p.nodeType === 1 && d < 8 && !bg; p = p.parentElement, d++) {
      for (const m of getComputedStyle(p).backgroundImage.matchAll(/url\((["']?)(.*?)\1\)/g)) {
        try { const u = new URL(m[2], document.baseURI).href; if (/^https?:/.test(u)) { bg = {base: document.baseURI, url: u}; break; } } catch (_) {}
      }
    }
  }
  return {src, link, image, bg};
}
