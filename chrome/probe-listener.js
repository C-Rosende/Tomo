let lastTarget = null;
addEventListener("contextmenu", (event) => { lastTarget = event.target; }, true);
chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message?.type !== "tomo-probe") return;
  reply(probeElement(lastTarget));
});