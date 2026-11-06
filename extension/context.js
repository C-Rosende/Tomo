function overVideo(x, y) {
  const area = innerWidth * innerHeight;
  for (const node of document.elementsFromPoint(x, y)) {
    if (node.localName === "video") return true;
    const rect = node.getBoundingClientRect();
    if (rect.width * rect.height < area * 0.9 && node.querySelector && node.querySelector("video")) return true;
  }
  return false;
}

window.addEventListener("contextmenu", (event) => {
  if (!event.shiftKey && overVideo(event.clientX, event.clientY)) event.stopImmediatePropagation();
}, true);
