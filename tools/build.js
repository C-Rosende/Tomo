const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const root = path.join(__dirname, "..");

const extensionFiles = ["background.js", "context.js", "detect.js", "i18n.js", "icon128.png",
  "icon48.png", "icon96.png", "image-ai.js", "images.js", "manifest.json", "platform.js",
  "popup.css", "popup.html", "popup.js", "probe.js", "report.js"];
const chromeFiles = ["compat.js", "manifest.json", "platform.js", "probe-listener.js", "sw.js"];
const runtimeFiles = ["LICENSE", "PRIVACY.md", "docs/TERMS.md", "install.ps1", "install.cmd",
  "install.sh", "uninstall.ps1", "chromium-ids.txt", "backend/host.py", "backend/sysdep.py",
  "backend/ai.py", "backend/locales.json", "backend/host.bat", "backend/requirements.lock",
  "backend/requirements-asr.lock"];
const publicFiles = ["README.md", "FAQ.md", "LICENSE", "PRIVACY.md", "SECURITY.md",
  "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md", "get.ps1", "uninstall.ps1",
  "docs/hero.svg", "docs/permissions.md", "docs/TERMS.md", ".github/FUNDING.yml",
  ".github/ISSUE_TEMPLATE/config.yml", ".github/ISSUE_TEMPLATE/bug_report.yml",
  ".github/ISSUE_TEMPLATE/suggestion.yml"];

function readEntries(base, names) {
  return new Map(names.map((name) => {
    let file = base;
    for (const part of name.split("/")) {
      file = path.join(file, part);
      if (fs.lstatSync(file).isSymbolicLink()) throw new Error(`Package symlink refused: ${name}`);
    }
    if (!fs.statSync(file).isFile()) throw new Error(`Package file required: ${name}`);
    return [name, fs.readFileSync(file)];
  }));
}

function extensionEntries() {
  const locales = fs.readdirSync(path.join(root, "extension", "_locales"), {withFileTypes: true})
    .filter((entry) => entry.isDirectory() && /^[a-z]{2,3}(?:_[A-Z]{2})?$/.test(entry.name))
    .map((entry) => `_locales/${entry.name}/messages.json`);
  return readEntries(path.join(root, "extension"), [...extensionFiles, ...locales]);
}

const wrapDetect = (source) => `globalThis.__tomoDetect = () => (${source.trim().replace(/;$/, "")});\n`;

function chromePackage() {
  const version = JSON.parse(fs.readFileSync(path.join(root, "extension", "manifest.json"), "utf8")).version;
  const entries = extensionEntries();
  for (const [name, data] of readEntries(path.join(root, "chrome"), chromeFiles)) entries.set(name, data);
  const unavailable = {en: "This page is not supported.", es: "Esta página no es compatible.",
    de: "Diese Seite wird nicht unterstützt.", fr: "Cette page n'est pas prise en charge.",
    it: "Questa pagina non è supportata.", pt: "Esta página não é compatível.",
    pl: "Ta strona nie jest obsługiwana.", ru: "Эта страница не поддерживается.",
    tr: "Bu sayfa desteklenmiyor.", ja: "このページには対応していません。",
    ro: "Această pagină nu este acceptată."};
  const missingRuntime = {en: "Media runtime missing", es: "Falta un componente multimedia",
    de: "Medienkomponente fehlt", fr: "Composant multimédia manquant",
    it: "Componente multimediale mancante", pt: "Falta um componente de mídia",
    pl: "Brak komponentu multimedialnego", ru: "Отсутствует мультимедийный компонент",
    tr: "Medya bileşeni eksik", ja: "メディア用コンポーネントがありません",
    ro: "Componenta media lipsește"};
  for (const name of [...entries.keys()].filter((n) => /^_locales\/[^/]+\/messages\.json$/.test(n))) {
    const lang = name.split("/")[1];
    if (!unavailable[lang] || !missingRuntime[lang]) throw new Error(`Chrome translations missing for ${lang}`);
    const messages = JSON.parse(entries.get(name).toString("utf8"));
    messages.siteUnavailable = {message: unavailable[lang]};
    messages.backendNoDeno = {message: missingRuntime[lang]};
    entries.set(name, Buffer.from(JSON.stringify(messages, null, 2) + "\n"));
  }
  const manifest = JSON.parse(entries.get("manifest.json").toString("utf8"));
  if (manifest.version !== version) throw new Error(`Chrome and Firefox manifest versions differ: ${manifest.version} / ${version}`);
  entries.set("manifest.json", Buffer.from(JSON.stringify(manifest, null, 2) + "\n"));
  entries.set("detect.js", Buffer.from(wrapDetect(entries.get("detect.js").toString("utf8"))));
  const popup = entries.get("popup.html").toString("utf8");
  const shimmed = popup.replace('<script src="i18n.js">', '<script src="compat.js"></script>\n  <script src="i18n.js">');
  if (shimmed === popup) throw new Error("popup.html: i18n.js script tag not found");
  entries.set("popup.html", Buffer.from(shimmed));
  return {version, entries};
}

function firefoxPackage() {
  const version = JSON.parse(fs.readFileSync(path.join(root, "extension", "manifest.json"), "utf8")).version;
  const entries = extensionEntries();
  return {version, entries};
}

function runtimePackage() {
  const version = JSON.parse(fs.readFileSync(path.join(root, "extension", "manifest.json"), "utf8")).version;
  const entries = new Map([...readEntries(root, runtimeFiles)].map(([name, data]) => [`Tomo-${version}/${name}`, data]));
  return {version, entries};
}

function stagePublic(out = path.join(root, "dist", "public")) {
  const entries = readEntries(root, publicFiles);
  if (fs.existsSync(out)) throw new Error("Public staging requires a new directory; existing files will not be overwritten.");
  fs.mkdirSync(out, {recursive: true});
  for (const [name, data] of entries) {
    const file = path.join(out, name);
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, data, {flag: "wx"});
  }
  return out;
}

function zip(entries) {
  const parts = [];
  const central = [];
  let offset = 0;
  const stamp = {time: 0, date: (46 << 9) | (1 << 5) | 1};
  for (const [name, data] of [...entries].sort(([a], [b]) => (a < b ? -1 : 1))) {
    const body = zlib.deflateRawSync(data, {level: 9});
    const nameBuf = Buffer.from(name);
    const crc = zlib.crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(8, 8);
    local.writeUInt16LE(stamp.time, 10);
    local.writeUInt16LE(stamp.date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    parts.push(local, nameBuf, body);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    local.copy(entry, 6, 4, 30);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, nameBuf);
    offset += local.length + nameBuf.length + body.length;
  }
  const centralBuf = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.size, 8);
  end.writeUInt16LE(entries.size, 10);
  end.writeUInt32LE(centralBuf.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, centralBuf, end]);
}

function build(out = path.join(root, "dist"), {release = false} = {}) {
  fs.mkdirSync(out, {recursive: true});
  const made = [];
  const packages = [["firefox", firefoxPackage], ["chrome", chromePackage]];
  if (release) packages.push(["source", runtimePackage]);
  for (const [label, make] of packages) {
    const {version, entries} = make();
    const file = path.join(out, `tomo-${label}-${version}.zip`);
    fs.writeFileSync(file, zip(entries));
    made.push(file);
  }
  return made;
}

module.exports = {chromePackage, firefoxPackage, runtimePackage, stagePublic, zip, build};

if (require.main === module) {
  const at = process.argv.indexOf("--out");
  if (at > 0 && (!process.argv[at + 1] || process.argv[at + 1].startsWith("--"))) throw new Error("--out requires a directory.");
  const out = at > 0 ? process.argv[at + 1] : undefined;
  const made = process.argv.includes("--public") ? [stagePublic(out)] : build(out, {release: process.argv.includes("--release")});
  for (const file of made) console.log(path.relative(process.cwd(), file));
}
