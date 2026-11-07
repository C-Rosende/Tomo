const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

test("locale files have no duplicate keys", () => {
  const dir = path.join(__dirname, "..", "extension", "_locales");
  for (const lang of fs.readdirSync(dir)) {
    const text = fs.readFileSync(path.join(dir, lang, "messages.json"), "utf8");
    const keys = [...text.matchAll(/^ "([^"]+)": \{/gm)].map((m) => m[1]);
    assert.strictEqual(new Set(keys).size, keys.length, `duplicate key in ${lang}`);
  }
});
