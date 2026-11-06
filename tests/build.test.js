const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const zlib = require("zlib");
const {chromePackage, firefoxPackage, runtimePackage, stagePublic, build} = require("../tools/build");

const text = (pkg, name) => pkg.entries.get(name).toString("utf8");
function assertZipValid(file) {
  const zip = fs.readFileSync(file);
  let offset = 0;
  let count = 0;
  const names = [];
  while (zip.readUInt32LE(offset) === 0x04034b50) {
    const method = zip.readUInt16LE(offset + 8);
    const crc = zip.readUInt32LE(offset + 14);
    const compressed = zip.readUInt32LE(offset + 18);
    const size = zip.readUInt32LE(offset + 22);
    const nameSize = zip.readUInt16LE(offset + 26);
    names.push(zip.toString("utf8", offset + 30, offset + 30 + nameSize));
    const extraSize = zip.readUInt16LE(offset + 28);
    const start = offset + 30 + nameSize + extraSize;
    const packed = zip.subarray(start, start + compressed);
    const data = method === 8 ? zlib.inflateRawSync(packed) : packed;
    assert.strictEqual(method === 8 || method === 0, true, `método ZIP ${method}`);
    assert.strictEqual(data.length, size, `tamaño de ${zip.toString("utf8", offset + 30, offset + 30 + nameSize)}`);
    assert.strictEqual(zlib.crc32(data), crc, "CRC ZIP");
    offset = start + compressed;
    count++;
  }
  assert.ok(count > 0, "ZIP sin archivos");
  assert.strictEqual(zip.readUInt32LE(offset), 0x02014b50, "directorio central ZIP ausente");
  assert.strictEqual(zip.readUInt32LE(zip.length - 22), 0x06054b50, "fin ZIP ausente");
  return names;
}
const chrome = chromePackage();
const firefox = firefoxPackage();
assert.ok(chrome.entries.has("_locales/ro/messages.json"));
assert.ok(firefox.entries.has("_locales/ro/messages.json"));
for (const pkg of [chrome, firefox]) {
  const manifest = JSON.parse(text(pkg, "manifest.json"));
  assert.strictEqual(manifest.icons["128"], "icon128.png");
  const icon = pkg.entries.get(manifest.icons["128"]);
  assert.ok(icon && icon.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex")));
  assert.strictEqual(icon.readUInt32BE(16), 128);
  assert.strictEqual(icon.readUInt32BE(20), 128);
}

const cm = JSON.parse(text(chrome, "manifest.json"));
assert.strictEqual(cm.manifest_version, 3);
assert.strictEqual(cm.version, JSON.parse(text(firefox, "manifest.json")).version);
assert.strictEqual(JSON.parse(fs.readFileSync(path.join(__dirname, "../chrome/manifest.json"), "utf8")).version, cm.version);
assert.ok(text(chrome, "popup.html").includes('<script src="report.js"></script>'));
assert.ok(chrome.entries.has("report.js") && firefox.entries.has("report.js"));
assert.ok(text(chrome, "popup.html").includes('<script src="platform.js"></script>'));
for (const key of ["browser_specific_settings", "browser_action", "applications"]) assert.ok(!(key in cm), key);
assert.ok(!cm.permissions.includes("menus") && cm.permissions.includes("contextMenus"));
assert.ok(!cm.permissions.includes("data_collection") && !cm.host_permissions);
assert.deepStrictEqual(cm.content_scripts[0].exclude_matches, ["*://*.youtube.com/*", "*://*.youtu.be/*", "*://*.youtube-nocookie.com/*", "*://*.ytimg.com/*"]);
assert.ok([...cm.permissions, ...cm.optional_permissions, ...(cm.host_permissions || []), ...cm.optional_host_permissions]
  .every((p) => !/youtube|youtu\.be|ytimg/i.test(p)));
for (const [name, data] of chrome.entries) {
  if (!/\.(?:js|json|html|css)$/.test(name)) continue;
  const mentions = /youtube|youtu\.be|ytimg/i.test(data.toString("utf8"));
  assert.ok(!mentions || ["platform.js", "manifest.json"].includes(name), `mención no permitida en Chrome: ${name}`);
}
assert.ok(!/imageVariants\([^)]*\)\s*\{[^}]*ytimg/i.test(text(chrome, "platform.js")), "Chrome no procesa miniaturas del sitio excluido");
const chromePlatform = new vm.Script(`${text(chrome, "platform.js")}; platform.blocks`);
const blocks = chromePlatform.runInNewContext({URL});
for (const url of ["https://youtube.com/watch?v=x", "https://m.youtube.com/shorts/x", "https://youtu.be/x", "https://www.youtube-nocookie.com/embed/x", "https://i.ytimg.com/vi/x/hqdefault.jpg"]) {
  assert.ok(blocks(url), `Chrome no bloquea ${url}`);
}
assert.ok(!blocks("https://notyoutube.com/video"));
assert.ok(!/youtube|youtu\.be/i.test(text(chrome, "popup.js")), "Chrome no detecta medios del sitio excluido");
for (const name of [...chrome.entries.keys()].filter((n) => n.startsWith("_locales/"))) {
  assert.ok(!/youtube|youtu\.be/i.test(text(chrome, name)), `locale Chrome: ${name}`);
}
const referenced = [cm.background.service_worker, ...cm.content_scripts.flatMap((c) => c.js), cm.action.default_popup,
  ...Object.values(cm.icons), ...Object.values(cm.action.default_icon)];
for (const name of referenced) assert.ok(chrome.entries.has(name), `falta ${name} en Chrome`);
const sw = text(chrome, "sw.js");
for (const [, list] of sw.matchAll(/importScripts\(([^)]*)\)/g)) {
  for (const [, name] of list.matchAll(/"([^"]+)"/g)) assert.ok(chrome.entries.has(name), `importScripts: falta ${name}`);
}

const fm = JSON.parse(text(firefox, "manifest.json"));
assert.strictEqual(fm.manifest_version, 2);
assert.ok(fm.permissions.includes("menus") && !fm.permissions.includes("contextMenus"));
assert.ok(!fm.content_scripts[0].exclude_matches);
assert.ok(/youtube/i.test(text(firefox, "platform.js")));
assert.strictEqual(fm.browser_specific_settings.gecko.id, "tomo@tomo.app");
const chromiumIds = fs.readFileSync(path.join(__dirname, "../chromium-ids.txt"), "utf8").split(/\r?\n/)
  .map((line) => line.replace(/#.*/, "").trim()).filter(Boolean);
assert.deepStrictEqual(chromiumIds, ["ejdlbopdblhemopclkioijjcnmlbenfp"]);
for (const name of fm.background.scripts) assert.ok(firefox.entries.has(name), `falta ${name} en Firefox`);
assert.ok(!firefox.entries.has("sw.js") && !firefox.entries.has("compat.js"));

for (const [name, data] of chrome.entries) {
  if (name.endsWith(".js")) assert.ok(!/getTargetElement|tabs\.executeScript/.test(data.toString("utf8")), `API de Firefox en ${name}`);
}

for (const [name, data] of chrome.entries) {
  if (name.endsWith(".js")) new vm.Script(data.toString("utf8"), {filename: `chrome/${name}`});
}
const sandbox = {globalThis: {}, document: {querySelectorAll: () => []}, location: {href: "https://example.com/"}};
sandbox.globalThis = sandbox;
vm.runInNewContext(text(chrome, "detect.js"), sandbox);
assert.strictEqual(typeof sandbox.__tomoDetect, "function");

const probe = text(firefox, "probe.js");
new vm.Script(`(${probe.slice(probe.indexOf("function probeElement"))})`);

const out = fs.mkdtempSync(path.join(__dirname, "../dist/tomo-build-"));
try {
  const made = build(out);
  assert.strictEqual(made.length, 2);
  for (const file of made) {
    assert.ok(fs.statSync(file).size > 1000);
    assertZipValid(file);
  }
  const release = build(out, {release: true});
  assert.strictEqual(release.length, 3);
  const runtime = runtimePackage();
  const prefix = `Tomo-${runtime.version}/`;
  const runtimeNames = assertZipValid(release.find((file) => path.basename(file).startsWith("tomo-source-")));
  assert.deepStrictEqual(runtimeNames, [...runtime.entries.keys()].sort());
  const installer = runtime.entries.get(`${prefix}install.ps1`).toString("utf8");
  const managed = installer.match(/\$files = @\(([^)]+)\)/)[1].matchAll(/'([^']+)'/g);
  for (const [, name] of managed) assert.ok(runtime.entries.has(`${prefix}backend/${name}`), `falta dependencia del instalador: ${name}`);
  for (const name of ["install.cmd", "install.sh", "uninstall.ps1", "chromium-ids.txt", "LICENSE", "docs/TERMS.md"]) {
    assert.ok(runtime.entries.has(prefix + name), `falta archivo de distribución: ${name}`);
  }
  for (const pkg of [chrome, firefox, runtime]) {
    assert.ok([...pkg.entries.keys()].every((name) => !/(?:^|\/)(?:\.git|\.env[^/]*|\.venv|tests|tools|AGENTS\.md|\x43LAUDE\.md|GEMINI\.md|config\.json|history\.json)(?:\/|$)/i.test(name)), "archivo privado en paquete");
  }
  const staged = stagePublic(path.join(out, "public"));
  const stagedFiles = fs.readdirSync(staged, {recursive: true}).filter((name) => fs.statSync(path.join(staged, name)).isFile());
  for (const forbidden of [".git", "backend", "extension", "chrome", "tests", "tools", ".github/workflows", "docs/DEVELOPMENT.md", "docs/RELEASING.md", "docs/PUBLICATION.md"]) {
    assert.ok(!fs.existsSync(path.join(staged, forbidden)), `archivo de desarrollo en exportación pública: ${forbidden}`);
  }
  for (const name of stagedFiles.filter((name) => name.endsWith(".md"))) {
    const content = fs.readFileSync(path.join(staged, name), "utf8");
    for (const [, target] of content.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
      if (/^(?:[a-z]+:|#)/i.test(target)) continue;
      assert.ok(fs.existsSync(path.resolve(path.dirname(path.join(staged, name)), target.split("#")[0])), `enlace público roto: ${name} -> ${target}`);
    }
  }
  const before = fs.readFileSync(path.join(staged, "README.md"));
  assert.throws(() => stagePublic(staged), /new directory/);
  assert.deepStrictEqual(fs.readFileSync(path.join(staged, "README.md")), before);
} finally {
  fs.rmSync(out, {recursive: true, force: true});
}
console.log("build.test.js OK");
