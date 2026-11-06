"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {tomoReport, tomoBrowserName, tomoIssueUrl} = require("../extension/report");

const privateText = "https://private.example.com/alice C:\\Users\\Alice\\secret.mp4";
const report = tomoReport({extensionVersion: "0.7.11", componentVersion: privateText,
  ytdlpVersion: "2026.10.01", ffmpeg: true, asr: false, failedCount: 3});
assert.match(report, /Tomo 0\.7\.11/);
assert.match(report, /Recent errors: 3/);
assert.doesNotMatch(report, /private|Alice|secret|\\|\//);

const popup = fs.readFileSync(path.join(__dirname, "../extension/popup.js"), "utf8");
assert.match(popup, /navigator\.clipboard\.writeText\(text\)/);
assert.match(popup, /browser\.tabs\.create\(\{url\}\)/);
assert.ok(!popup.includes("report: text"));
assert.ok(!popup.includes("github.com/C-Rosende/Tomo/issues"));

assert.equal(tomoBrowserName("Mozilla/5.0 (Windows NT 10.0; rv:140.0) Gecko/20100101 Firefox/140.0", false), "Firefox");
assert.equal(tomoBrowserName("Mozilla/5.0 AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0", false), "Edge");
assert.equal(tomoBrowserName("Mozilla/5.0 AppleWebKit/537.36 Chrome/130.0 Safari/537.36", true), "Brave");
assert.equal(tomoBrowserName("Mozilla/5.0 AppleWebKit/537.36 Chrome/130.0 Safari/537.36", false), "Chrome");

const issue = new URL(tomoIssueUrl({extensionVersion: "0.7.12", componentVersion: privateText, os: "Windows", browser: "Brave"}));
assert.equal(issue.origin + issue.pathname, "https://github.com/C-Rosende/Tomo/issues/new");
assert.equal(issue.searchParams.get("template"), "bug_report.yml");
assert.equal(issue.searchParams.get("extension_version"), "0.7.12");
assert.equal(issue.searchParams.get("helper_version"), "not detected");
assert.doesNotMatch(issue.search, /private|Alice|secret/);
const fallback = new URL(tomoIssueUrl({extensionVersion: "x", componentVersion: "1.2.3", os: "Plan9", browser: "Lynx"}));
assert.equal(fallback.searchParams.get("os"), "Other");
assert.equal(fallback.searchParams.get("browser"), "Other");
assert.equal(fallback.searchParams.get("helper_version"), "1.2.3");

const form = fs.readFileSync(path.join(__dirname, "../.github/ISSUE_TEMPLATE/bug_report.yml"), "utf8");
const ids = [...form.matchAll(/^\s+id: (\S+)$/gm)].map((m) => m[1]);
for (const key of issue.searchParams.keys()) if (!["template", "title"].includes(key)) assert.ok(ids.includes(key), key);
const options = (id) => form.split(/\n  - type: /).find((block) => block.includes(`id: ${id}\n`))
  .split("options:")[1].split("validations:")[0].match(/- (.+)/g).map((line) => line.slice(2).trim());
for (const os of ["Windows", "macOS", "Linux", "Other"]) assert.ok(options("os").includes(os), os);
for (const name of ["Firefox", "Chrome", "Edge", "Brave", "Other"]) assert.ok(options("browser").includes(name), name);
