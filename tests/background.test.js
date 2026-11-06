const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..", "extension");

function load(stored = {}, extra = {}, chromium = false) {
  const menus = new Map();
  const sent = [];
  const handlers = {};
  const listener = (name) => ({addListener: (fn) => { handlers[name] = fn; }, removeListener() {}});
  const noop = () => Promise.resolve();
  const browser = {
    i18n: {getUILanguage: () => "es", getMessage: (key) => key},
    runtime: {id: "x", getURL: (p) => `moz-extension://x/${p}`, onInstalled: listener("installed"),
      onConnect: listener("connect"), onMessage: listener("message"),
      connectNative: () => {
        const port = {onMessage: listener("native"), onDisconnect: listener("disconnect"),
          postMessage: (m) => {
            if (extra.imageHelper) {
              sent.push(m);
              queueMicrotask(() => handlers.native({status: "done", protocol: 2, path: "C:/Tomo/Image.jpg"}));
            } else if (extra.nativePost) extra.nativePost(m);
            else sent.push(m);
          },
          disconnect() { extra.nativeDisconnect?.(); }};
        return port;
      }},
    storage: {local: {get: (defaults) => Promise.resolve({...defaults, ...stored}), set: noop}},
    menus: {
      removeAll: () => { menus.clear(); return Promise.resolve(); },
      create: (o) => menus.set(o.id, {...o}),
      update: (id, o) => { Object.assign(menus.get(id) || {}, o); return Promise.resolve(); },
      onClicked: listener("clicked"), getTargetElement: () => null},
    notifications: {create: noop, onClicked: listener("notificationClicked")},
    browserAction: {setBadgeText() {}, setTitle() {}, setBadgeBackgroundColor() {}, openPopup: noop},
    tabs: {create: noop, executeScript: noop, get: async (id) => ({id, url: "https://example.com/"}),
      query: async () => [{id: 1, url: "https://example.com/"}],
      onActivated: listener("tabActivated"), onUpdated: listener("tabUpdated")},
    downloads: {download: noop, erase: noop, onChanged: listener("downloadChanged")},
    alarms: {get: () => Promise.resolve({}), create() {}, onAlarm: listener("alarm")},
    ...extra,
  };
  browser.contextMenus = browser.menus;
  const context = vm.createContext({browser, chrome: browser, console, URL, Intl, setTimeout, clearTimeout, setInterval: () => 0, fetch: () => Promise.reject(new Error("no red"))});
  for (const file of ["probe.js", "platform.js", "i18n.js", "images.js", "background.js"]) {
    const source = chromium && file === "platform.js" ? path.join(__dirname, "..", "chrome", file) : path.join(root, file);
    vm.runInContext(fs.readFileSync(source, "utf8"), context, {filename: file});
  }
  return {menus, sent, handlers, context, send: (msg, sender = {id: "x", url: "moz-extension://x/popup.html"}) => handlers.message(msg, sender)};
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

(async () => {
  let app = load();
  await settle();
  assert.ok([...app.menus.keys()].indexOf("dl-default") === 1, "dl-default va justo después de root");
  assert.strictEqual(app.menus.get("dl-default").title, "menuDefault: menuBest");

  let prefs = await app.send({type: "setPrefs", prefs: {defaultPreset: "mp3"}});
  assert.strictEqual(prefs.defaultPreset, "mp3");
  assert.strictEqual(app.menus.get("dl-default").title, "menuDefault: menuMp3");

  for (const bad of ["rm -rf", "", null, 7, ["mp4"], {preset: "mp4"}, "__proto__", "constructor"]) {
    prefs = await app.send({type: "setPrefs", prefs: {defaultPreset: bad}});
    assert.strictEqual(prefs.defaultPreset, "best", `rechaza ${JSON.stringify(bad)}`);
  }

  for (const preset of ["best", "mp4", "mkv", "audio", "m4a", "mp3"]) {
    app = load({prefs: {defaultPreset: preset, showProgress: false}});
    await settle();
    app.handlers.clicked({menuItemId: "dl-default", pageUrl: "https://example.com/watch?v=1"}, {id: 1, url: "https://example.com/watch?v=1", title: "t"});
    await settle();
    const job = app.sent.find((m) => m.action === "download");
    assert.ok(job, `descarga enviada para ${preset}`);
    assert.strictEqual(job.preset, preset);
  }

  app = load();
  await settle();
  app.handlers.clicked({menuItemId: "dl-default", pageUrl: "about:blank"}, {id: 1, url: "about:blank"});
  await settle();
  assert.strictEqual(app.sent.length, 0);

  app = load({prefs: {showProgress: false}}, {}, true);
  await settle();
  app.handlers.tabUpdated(1, {}, {id: 1, active: true, url: "https://www.youtube.com/watch?v=test"});
  await settle();
  assert.strictEqual(app.menus.get("root").visible, false, "Chrome oculta el menú en la pestaña excluida");
  assert.strictEqual(app.menus.get("img-save").visible, false, "Chrome oculta también la imagen");
  app.handlers.tabUpdated(1, {}, {id: 1, active: true, url: "https://example.com/video"});
  await settle();
  assert.strictEqual(app.menus.get("root").visible, true, "Chrome restaura el menú en otros sitios");
  app.handlers.clicked({menuItemId: "dl-default", pageUrl: "https://www.youtube.com/watch?v=test"},
    {id: 1, url: "https://www.youtube.com/watch?v=test", title: "t"});
  await settle();
  assert.strictEqual(app.sent.length, 0, "Chromium no envía descargas de YouTube al componente");
  assert.strictEqual((await app.send({type: "start", request: {action: "download", url: "https://youtu.be/test"}})), null);
  assert.strictEqual(app.sent.length, 0, "Chromium tampoco inicia YouTube desde el popup");
  app.handlers.clicked({menuItemId: "img-save", pageUrl: "https://example.com/", srcUrl: "https://i.ytimg.com/vi/x/hqdefault.jpg"},
    {id: 1, url: "https://example.com/"});
  await settle();
  assert.strictEqual(app.sent.length, 0, "Chromium no procesa imágenes del dominio excluido");

  app = load({prefs: {defaultPreset: "../../x"}});
  await settle();
  assert.strictEqual(app.menus.get("dl-default").title, "menuDefault: menuBest");

  app = load({prefs: {ai: {provider: "openai", key: "TEST_ONLY"}}});
  await settle();
  assert.strictEqual(await app.send({type: "state"}, {id: "x", url: "https://example.com/", tab: {id: 9}}), undefined);
  assert.strictEqual(await app.send({type: "state"}, {id: "other", url: "moz-extension://x/popup.html"}), undefined);
  assert.strictEqual((await app.send({type: "state"})).prefs.ai.key, "TEST_ONLY");

  let consent = false;
  const permissions = {contains: async () => consent, onAdded: {addListener() {}}, onRemoved: {addListener() {}}};
  app = load({}, {permissions});
  await settle();
  consent = true;
  await app.send({type: "setPrefs", prefs: {ai: {provider: "openai", key: "TEST_ONLY"}}});
  assert.ok(await app.send({type: "start", request: {action: "transcript", mode: "summary", url: "https://example.com/v"}}),
    "el consentimiento del proveedor nuevo permite iniciar la tarea inmediatamente");

  app = load({}, {nativePost: () => { throw new Error("native write failed"); }});
  await settle();
  await app.send({type: "start", request: {action: "download", url: "https://example.com/v"}});
  await settle();
  assert.strictEqual((await app.send({type: "state"})).jobs[0].state, "error", "fallo al enviar no deja la tarea ejecutándose");
  let disconnected = 0;
  app = load({}, {nativePost: () => { throw new Error("native write failed"); }, nativeDisconnect: () => disconnected++});
  await settle();
  assert.match((await app.send({type: "ping"})).error, /native write failed/);
  assert.strictEqual(disconnected, 1, "fallo al enviar una llamada nativa desconecta el puerto");

  const jar = {A: [], B: [{domain: "site.tld", hostOnly: true, path: "/", secure: true, name: "sid", value: "FROM_B", httpOnly: false}]};
  const cookies = {getAllCookieStores: () => Promise.resolve([{id: "A"}, {id: "B"}]),
    getAll: ({storeId}) => Promise.resolve(jar[storeId])};
  const extra = {cookies, tabs: {create: () => Promise.resolve(), executeScript: () => Promise.resolve(), query: () => Promise.resolve([{id: 1, cookieStoreId: "A"}])}};
  app = load({prefs: {showProgress: false, session: true}}, extra);
  await settle();
  const page = "https://site.tld/v";
  app.handlers.clicked({menuItemId: "dl-default", pageUrl: page}, {id: 1, url: page, cookieStoreId: "A"});
  await settle();
  let sent = app.sent.find((m) => m.action === "download");
  assert.ok(sent && !sent.cookies && !("store" in sent), "el contenedor A no recibe cookies del B");
  app = load({prefs: {showProgress: false, session: true}}, extra);
  await settle();
  app.handlers.clicked({menuItemId: "dl-default", pageUrl: page}, {id: 2, url: page, cookieStoreId: "B"});
  await settle();
  sent = app.sent.find((m) => m.action === "download");
  assert.ok(sent && /FROM_B/.test(sent.cookies) && !("store" in sent), "el contenedor B sí envía su sesión");
  app = load({prefs: {showProgress: false}});
  await settle();
  const posts = [];
  app.handlers.connect({name: "panel", sender: {id: "x", url: "moz-extension://x/popup.html"},
    postMessage: (m) => posts.push(m), onDisconnect: {addListener() {}}});
  app.handlers.clicked({menuItemId: "dl-default", pageUrl: "https://example.com/v"}, {id: 1, url: "https://example.com/v", title: "t"});
  await settle();
  const before = posts.length;
  for (let i = 1; i <= 40; i++) app.handlers.native({status: "progress", stage: "download", percent: i, detail: "x"});
  app.handlers.native({status: "done", message: "ok", path: "C:/d/a.mp4"});
  await new Promise((resolve) => setTimeout(resolve, 600));
  const jobs = posts.filter((m) => m.type === "jobs");
  assert.ok(jobs.length - before <= 4, `ráfaga agrupada (${jobs.length - before})`);
  assert.strictEqual(jobs.at(-1).jobs[0].state, "done", "el estado final llega al panel");
  const site = "https://galeria.cl/m/";
  const shown = {base: site, src: site + "t/1.jpg", link: "", data: [], srcset: [site + "g/big.jpg 2000w"]};
  const a = load({}, {imageHelper: true, tabs: {create: () => Promise.resolve(), executeScript: async () => [{image: shown}]}});
  await settle();
  a.handlers.clicked({menuItemId: "img-save-original", srcUrl: shown.src, targetElementId: 7, frameId: 0}, {id: 3, url: site});
  await settle();
  assert.strictEqual(a.sent.find((m) => m.action === "save-image")?.url, site + "g/big.jpg");
  const imageCount = a.sent.length;
  a.handlers.clicked({menuItemId: "img-save-original", srcUrl: "http://127.0.0.1/cam.jpg", frameId: 0}, {id: 3, url: site});
  await settle();
  assert.strictEqual(a.sent.length, imageCount, "private image never reaches native helper");
  console.log("background.test.js OK");
  process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
