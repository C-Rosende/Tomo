"use strict";

const UI_LOCALES = ["en", "es", "pt", "fr", "de", "it", "ru", "pl", "tr", "ja", "ro"];
const uiLang = () => {
  const lang = browser.i18n.getUILanguage().toLowerCase();
  return UI_LOCALES.includes(lang) ? lang : UI_LOCALES.includes(lang.split("-")[0]) ? lang.split("-")[0] : "en";
};
const I18N = {lang: uiLang(), msgs: null};

async function setUiLang(choice) {
  const code = UI_LOCALES.includes(choice) ? choice : "";
  I18N.msgs = null;
  I18N.lang = code || uiLang();
  if (!code) return;
  try {
    const data = await (await fetch(browser.runtime.getURL(`_locales/${code}/messages.json`))).json();
    I18N.msgs = Object.fromEntries(Object.entries(data).map(([key, value]) => [key, value.message]));
  } catch (_) {
    I18N.lang = uiLang();
  }
}

function t(key, ...subs) {
  if (!I18N.msgs) return browser.i18n.getMessage(key, subs.map(String)) || key;
  return (I18N.msgs[key] || browser.i18n.getMessage(key) || key).replace(/\$(\d)/g, (_, n) => String(subs[n - 1] ?? ""));
}
