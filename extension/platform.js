const platform = {
  blocks: () => false,
  imageVariants(u) {
    if (!/(^|\.)ytimg\.com$/.test(u.hostname)) return [];
    const m = u.pathname.match(/^\/(vi|vi_webp)\/([\w-]+)\/\w+?(?:default|\d)\.(jpg|webp)$/);
    return m ? ["maxresdefault", "sddefault", "hqdefault"].map((n) => `https://${u.hostname}/${m[1]}/${m[2]}/${n}.${m[3]}`) : [];
  },
  embed: (url) => /youtube(-nocookie)?\.com\/embed/.test(url || ""),
  preview(url, page) {
    const yt = /(?:youtube(?:-nocookie)?\.com\/(?:watch\?(?:.*&)?v=|shorts\/|embed\/|live\/)|youtu\.be\/)([\w-]{11})/.exec(url || "");
    if (yt) return {...(page || {}), image: `https://i.ytimg.com/vi/${yt[1]}/hqdefault.jpg`};
    return page?.image || page?.title ? page : null;
  },

  canProbe: (info, tab) => info.targetElementId != null && !!tab?.id,

  async probe(info, tab) {
    const code = `(${probeElement.toString()})(browser.menus.getTargetElement(${Number(info.targetElementId)}))`;
    const [found] = await browser.tabs.executeScript(tab.id, {frameId: info.frameId || 0, code});
    return found;
  },

  async detect(tabId) {
    let frames = [];
    try {
      frames = await browser.tabs.executeScript(tabId, {file: "detect.js", allFrames: true});
    } catch (_) {
      try { frames = await browser.tabs.executeScript(tabId, {file: "detect.js"}); }
      catch (_) { frames = []; }
    }
    return frames.filter(Boolean);
  },

  every(fn, firstMs, periodMs) {
    setTimeout(fn, firstMs);
    setInterval(fn, periodMs);
  },
};
if (typeof module !== "undefined") module.exports = {platform};
