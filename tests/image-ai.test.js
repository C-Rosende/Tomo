const assert = require("node:assert/strict");
const {imageAiProvider, imageAiMask, imageAiValidate, imageAiGenerate} = require("../extension/image-ai.js");

(async () => {
  const replicate = "r8_12345678abcd";
  const gemini = "AIza12345678abcd";
  assert.equal(imageAiProvider(replicate), "replicate");
  assert.equal(imageAiProvider(gemini), "gemini");
  assert.equal(imageAiProvider("wrong"), "");
  assert.equal(imageAiMask(replicate), "r8_…abcd");
  const requests = [];
  const ok = async (url, options) => { requests.push([url, options]); return {ok: true, status: 200}; };
  await imageAiValidate(replicate, ok);
  await imageAiValidate(gemini, ok);
  assert.equal(requests[0][0], "https://api.replicate.com/v1/account");
  assert.equal(requests[1][0], "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1");
  assert.equal(requests[0][1].headers.Authorization, `Bearer ${replicate}`);
  assert.equal(requests[1][1].headers["x-goog-api-key"], gemini);
  await assert.rejects(imageAiValidate(replicate, async () => ({ok: false, status: 401})), /invalid/);
  await assert.rejects(imageAiValidate(replicate, async () => ({ok: false, status: 402})), /credit/);
  await assert.rejects(imageAiValidate(replicate, async () => { throw Error("network"); }), /offline/);
  const generated = await imageAiGenerate({key: replicate, provider: "replicate"}, "data:image/png;base64,AA==", true,
    async (url, options) => {
      assert.equal(url, "https://api.replicate.com/v1/models/topazlabs/image-upscale/predictions");
      assert.equal(JSON.parse(options.body).input.image, "data:image/png;base64,AA==");
      return {ok: true, json: async () => ({status: "succeeded", output: "https://replicate.delivery/out.png"})};
    });
  assert.equal(generated.url, "https://replicate.delivery/out.png");
  for (const output of ["https://evil.example/out.png", "https://replicate.delivery.evil.example/x.png", "http://replicate.delivery/x.png"]) {
    await assert.rejects(imageAiGenerate({key: replicate, provider: "replicate"}, "data:image/png;base64,AA==", false,
      async () => ({ok: true, json: async () => ({status: "succeeded", output})})), /offline/);
  }
  console.log("image-ai.test.js OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
