const ALARM = "tomo-update";

const YOUTUBE = /^(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com|ytimg\.com)$/;

const platform = {
  requiresJsRuntime: false,
  blocks(url) {
    try { return YOUTUBE.test(new URL(url).hostname.toLowerCase()); }
    catch (_) { return false; }
  },
  embed: () => false,
  imageVariants: () => [],
  preview: (_url, page) => page?.image || page?.title ? page : null,

  watchMenus() {
    const refresh = async (tab) => {
      const visible = !platform.blocks(tab?.url);
      await Promise.all(["root", "img-save"].map((id) => chrome.contextMenus.update(id, {visible}).catch(() => {})));
    };
    chrome.tabs.onActivated.addListener(async ({tabId}) => refresh(await chrome.tabs.get(tabId).catch(() => null)));
    chrome.tabs.onUpdated.addListener((_id, _change, tab) => { if (tab.active) refresh(tab); });
    chrome.tabs.query({active: true, currentWindow: true}).then(([tab]) => refresh(tab)).catch(() => {});
  },

  canProbe: (info, tab) => !!tab?.id,

  async probe(info, tab) {
    if (platform.blocks(info.pageUrl) || platform.blocks(info.frameUrl) || platform.blocks(tab?.url)) return null;
    return chrome.tabs.sendMessage(tab.id, {type: "tomo-probe"}, {frameId: info.frameId || 0});
  },

  async detect(tabId) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (!tab || platform.blocks(tab.url)) return [];
    const run = async (allFrames) => {
      await chrome.scripting.executeScript({target: {tabId, allFrames}, files: ["detect.js"]});
      const results = await chrome.scripting.executeScript({target: {tabId, allFrames}, func: () => globalThis.__tomoDetect()});
      return results.map((r) => r.result);
    };
    try { return (await run(true)).filter(Boolean); }
    catch (_) {
      try { return (await run(false)).filter(Boolean); }
      catch (_) { return []; }
    }
  },

  every(fn, firstMs, periodMs) {
    chrome.alarms.get(ALARM).then((existing) => {
      if (!existing) chrome.alarms.create(ALARM, {delayInMinutes: firstMs / 60000, periodInMinutes: periodMs / 60000});
    }).catch(() => {});
    chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === ALARM) fn(); });
  },
};
