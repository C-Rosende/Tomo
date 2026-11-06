"use strict";

const ISSUE_FORM = "https://github.com/C-Rosende/Tomo/issues/new";
const ISSUE_SYSTEMS = ["Windows", "macOS", "Linux"];
const ISSUE_BROWSERS = ["Firefox", "Chrome", "Edge", "Brave"];

const reportVersion = (value) => /^[0-9][A-Za-z0-9._+-]{0,39}$/.test(String(value)) ? String(value) : "-";

function tomoReport({extensionVersion, componentVersion, ytdlpVersion, ffmpeg, asr, failedCount}) {
  return [
    `Tomo ${reportVersion(extensionVersion)} · component ${reportVersion(componentVersion)} · yt-dlp ${reportVersion(ytdlpVersion)}`,
    `ffmpeg ${Boolean(ffmpeg)} · asr ${Boolean(asr)}`,
    `Recent errors: ${Math.min(999, Math.max(0, Number(failedCount) || 0))}`,
  ].join("\n");
}

function tomoBrowserName(userAgent, isBrave) {
  if (/Firefox\//.test(userAgent)) return "Firefox";
  if (/Edg\//.test(userAgent)) return "Edge";
  if (isBrave) return "Brave";
  return /Chrome\//.test(userAgent) ? "Chrome" : "Other";
}

function tomoIssueUrl({extensionVersion, componentVersion, os, browser}) {
  const helper = reportVersion(componentVersion);
  const query = new URLSearchParams({
    template: "bug_report.yml",
    title: "Problem: ",
    os: ISSUE_SYSTEMS.includes(os) ? os : "Other",
    browser: ISSUE_BROWSERS.includes(browser) ? browser : "Other",
    extension_version: reportVersion(extensionVersion),
    helper_version: helper === "-" ? "not detected" : helper,
  });
  return `${ISSUE_FORM}?${query}`;
}

globalThis.tomoReport = tomoReport;
globalThis.tomoBrowserName = tomoBrowserName;
globalThis.tomoIssueUrl = tomoIssueUrl;
if (typeof module !== "undefined") module.exports = {tomoReport, tomoBrowserName, tomoIssueUrl};
