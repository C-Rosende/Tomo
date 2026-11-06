"use strict";

const IMAGE_AI_ORIGINS = {replicate: "https://api.replicate.com/*", gemini: "https://generativelanguage.googleapis.com/*"};

function imageAiProvider(key) {
  if (typeof key !== "string" || key.length > 400) return "";
  if (/^r8_[A-Za-z0-9_-]{8,}$/.test(key)) return "replicate";
  if (/^AIza[A-Za-z0-9_-]{8,}$/.test(key)) return "gemini";
  return "";
}

function imageAiMask(key) { return `${key.slice(0, key.startsWith("r8_") ? 3 : 4)}…${key.slice(-4)}`; }

const IMAGE_AI_MAX_DATA = 28 * 1024 * 1024;
const IMAGE_AI_DATA = /^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+={0,2})$/;

async function imageAiRequest(fetcher, url, options, json = true) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  try {
    const response = await fetcher(url, {...options, redirect: "error", credentials: "omit",
      referrerPolicy: "no-referrer", cache: "no-store", signal: controller.signal});
    if ([400, 401, 403].includes(response.status)) throw new Error("invalid");
    if ([402, 429].includes(response.status)) throw new Error("credit");
    if (!response.ok || response.redirected) throw new Error("offline");
    if (!json) { await response.body?.cancel(); return; }
    if (Number(response.headers?.get("content-length")) > IMAGE_AI_MAX_DATA) throw new Error("offline");
    if (!response.body) return await response.json();
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let size = 0, text = "";
    try {
      for (;;) {
        const {done, value} = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > IMAGE_AI_MAX_DATA) throw new Error("offline");
        text += decoder.decode(value, {stream: true});
      }
      return JSON.parse(text + decoder.decode());
    } finally { await reader.cancel().catch(() => {}); }
  } catch (error) {
    throw new Error(["invalid", "credit"].includes(error.message) ? error.message : "offline");
  } finally { clearTimeout(timer); }
}

async function imageAiValidate(key, fetcher = fetch) {
  const provider = imageAiProvider(key);
  if (!provider) throw new Error("invalid");
  const url = provider === "replicate" ? "https://api.replicate.com/v1/account"
    : "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1";
  const headers = provider === "replicate" ? {Authorization: `Bearer ${key}`} : {"x-goog-api-key": key};
  await imageAiRequest(fetcher, url, {headers}, false);
  return {provider, key};
}

async function imageAiGenerate(config, imageData, quality = false, fetcher = fetch) {
  const {key, provider} = config || {};
  if (!provider || imageAiProvider(key) !== provider || typeof imageData !== "string" ||
      imageData.length > 3 * 1024 * 1024 || !IMAGE_AI_DATA.test(imageData)) throw new Error("invalid");
  if (provider === "replicate") {
    const model = quality ? "topazlabs/image-upscale" : "nightmareai/real-esrgan";
    let prediction = await imageAiRequest(fetcher, `https://api.replicate.com/v1/models/${model}/predictions`, {
      method: "POST", headers: {Authorization: `Bearer ${key}`, "Content-Type": "application/json", Prefer: "wait=60"},
      body: JSON.stringify({input: {image: imageData}})
    });
    if (!prediction || typeof prediction !== "object") throw new Error("offline");
    for (let attempt = 0; attempt < 30 && !["succeeded", "failed", "canceled"].includes(prediction.status); attempt++) {
      let poll;
      try { poll = new URL(prediction.urls?.get || ""); } catch (_) { throw new Error("offline"); }
      if (poll.origin !== "https://api.replicate.com" || poll.username || poll.password ||
          !poll.pathname.startsWith("/v1/predictions/")) throw new Error("offline");
      await new Promise((resolve) => setTimeout(resolve, 1000));
      prediction = await imageAiRequest(fetcher, poll.href, {headers: {Authorization: `Bearer ${key}`}});
      if (!prediction || typeof prediction !== "object") throw new Error("offline");
    }
    if (prediction.status !== "succeeded") throw new Error("offline");
    const result = Array.isArray(prediction.output) ? prediction.output[0] : prediction.output;
    let output;
    try { output = new URL(typeof result === "string" ? result : ""); } catch (_) { throw new Error("offline"); }
    if (output.protocol !== "https:" || output.username || output.password ||
        !/(^|\.)replicate\.delivery$/.test(output.hostname)) throw new Error("offline");
    return {url: output.href};
  }
  const [, mime, base64] = IMAGE_AI_DATA.exec(imageData) || [];
  if (!mime) throw new Error("invalid");
  const data = await imageAiRequest(fetcher, "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent", {
    method: "POST", headers: {"x-goog-api-key": key, "Content-Type": "application/json"},
    body: JSON.stringify({contents: [{parts: [{text: "Upscale this image. Preserve its content and composition."},
      {inline_data: {mime_type: mime, data: base64}}]}], generationConfig: {responseModalities: ["IMAGE", "TEXT"]}})
  });
  const parts = Array.isArray(data?.candidates) ? data.candidates.flatMap((c) => Array.isArray(c?.content?.parts) ? c.content.parts : []) : [];
  const part = parts.find((p) => p?.inlineData?.data);
  const result = part ? `data:${part.inlineData.mimeType};base64,${part.inlineData.data}` : "";
  if (result.length > IMAGE_AI_MAX_DATA || !IMAGE_AI_DATA.test(result)) throw new Error("offline");
  return {data: result};
}

if (typeof module !== "undefined") module.exports = {imageAiProvider, imageAiMask, imageAiValidate, imageAiGenerate};
