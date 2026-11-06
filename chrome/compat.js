chrome.menus = chrome.contextMenus;
chrome.browserAction = chrome.action;

(() => {
  const KEY = "aiConsent";
  const isConsent = (p) => Boolean(p && p.data_collection);
  const wrongMix = (p) => { if (p.permissions || p.origins) throw new TypeError("data_collection cannot be mixed with other permissions"); };

  const select = () => globalThis.document?.getElementById("ai-provider");
  async function provider() {
    const el = select();
    if (el) return {id: el.value, name: el.selectedOptions?.[0]?.textContent?.trim() || el.value};
    const {prefs} = await chrome.storage.local.get("prefs");
    return {id: prefs?.ai?.provider || "", name: prefs?.ai?.provider || ""};
  }
  const given = async () => {
    const [{id}, stored] = [await provider(), await chrome.storage.local.get(KEY)];
    return Boolean(id) && stored[KEY]?.provider === id;
  };

  const listeners = {added: new Set(), removed: new Set()};
  const events = (set) => ({
    addListener: (fn) => set.add(fn),
    removeListener: (fn) => set.delete(fn),
    hasListener: (fn) => set.has(fn),
  });
  chrome.permissions.onAdded.addListener((p) => listeners.added.forEach((fn) => fn(p)));
  chrome.permissions.onRemoved.addListener((p) => listeners.removed.forEach((fn) => fn(p)));
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes[KEY]) return;
    const set = changes[KEY].newValue ? listeners.added : listeners.removed;
    set.forEach((fn) => fn({data_collection: ["websiteContent"]}));
  });

  const permissions = {
    contains: (p) => (isConsent(p) ? (wrongMix(p), given()) : chrome.permissions.contains(p)),
    request: async (p) => {
      if (!isConsent(p)) return chrome.permissions.request(p);
      wrongMix(p);
      if (await given()) return true;
      const {id, name} = await provider();
      if (!id || !globalThis.confirm?.(chrome.i18n.getMessage("aiConsentPrompt", name))) return false;
      await chrome.storage.local.set({[KEY]: {provider: id, version: 1}});
      return true;
    },
    remove: async (p) => {
      if (!isConsent(p)) return chrome.permissions.remove(p);
      await chrome.storage.local.remove(KEY);
      return true;
    },
    onAdded: events(listeners.added),
    onRemoved: events(listeners.removed),
  };
  globalThis.browser = new Proxy(chrome, {get: (target, name) => (name === "permissions" ? permissions : target[name])});
})();
