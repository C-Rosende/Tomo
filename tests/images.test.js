const assert = require("assert");
const {maxImageUrls: f} = require("../extension/images.js");
const {platform} = require("../extension/platform.js");
const last = (u) => u.split("/").pop();

assert.deepStrictEqual(f("https://pbs.twimg.com/media/GabC-d_1.jpg?format=jpg&name=small"), [
  "https://pbs.twimg.com/media/GabC-d_1?format=jpg&name=orig",
  "https://pbs.twimg.com/media/GabC-d_1.jpg?format=jpg&name=small"]);
assert.strictEqual(f("https://pbs.twimg.com/media/GabC.png:large")[0], "https://pbs.twimg.com/media/GabC?format=png&name=orig");
assert.deepStrictEqual(f("https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", platform.imageVariants).map(last), ["maxresdefault.jpg", "sddefault.jpg", "hqdefault.jpg"]);
assert.strictEqual(f("https://i.pinimg.com/236x/ab/cd/ef.jpg")[0], "https://i.pinimg.com/originals/ab/cd/ef.jpg");
assert.strictEqual(f("https://i.pinimg.com/474x/ab/cd/ef.jpg")[0], "https://i.pinimg.com/originals/ab/cd/ef.jpg");
assert.strictEqual(f("https://preview.redd.it/abc.jpeg?width=640&auto=webp&s=x")[0], "https://i.redd.it/abc.jpeg");
assert.strictEqual(f("https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Foo.jpg/300px-Foo.jpg")[0], "https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.jpg");
assert.strictEqual(f("https://thumb.wikimedia.org/wikipedia/commons/thumb/2/25/Siam_lilacpoint.jpg/250px-Siam_lilacpoint.jpg?utm_source=en.wikipedia.org")[0], "https://upload.wikimedia.org/wikipedia/commons/2/25/Siam_lilacpoint.jpg");
assert.strictEqual(f("https://i.imgur.com/tUVsNPJ_d.webp?maxwidth=520&shape=thumb&fidelity=high")[0], "https://i.imgur.com/tUVsNPJ.jpg");
assert.strictEqual(f("https://i.imgur.com/AbCdEfGl.jpg")[0], "https://i.imgur.com/AbCdEfG.jpg");
assert.strictEqual(f("https://i.imgur.com/AbC12s.jpg")[0], "https://i.imgur.com/AbC12.jpg");
for (const suffix of ["s", "m", "l", "t"]) assert.strictEqual(f(`https://i.imgur.com/AbCdEfG${suffix}.jpg`)[0], "https://i.imgur.com/AbCdEfG.jpg");
assert.strictEqual(f("https://media2.giphy.com/media/AbC123/200.gif")[0], "https://media2.giphy.com/media/AbC123/giphy.gif");
assert.strictEqual(f("https://media.giphy.com/media/AbC123/downsized_large.mp4")[0], "https://media.giphy.com/media/AbC123/giphy.mp4");
assert.strictEqual(f("https://images.unsplash.com/photo-123?w=300&h=200&fit=crop&q=90")[0], "https://images.unsplash.com/photo-123?q=90");
assert.strictEqual(f("https://images.pexels.com/photos/123/photo.jpg?w=300&h=200&fit=crop")[0], "https://images.pexels.com/photos/123/photo.jpg");
assert.strictEqual(f("https://64.media.tumblr.com/abc/tumblr_pic_400.jpg")[0], "https://64.media.tumblr.com/abc/tumblr_pic_1280.jpg");
assert.strictEqual(f("https://64.media.tumblr.com/abc/s640x960/pic.jpg")[0], "https://64.media.tumblr.com/abc/s2048x3072/pic.jpg");
assert.strictEqual(f("https://sitio.cl/wp-content/uploads/foto-300x200.jpg")[0], "https://sitio.cl/wp-content/uploads/foto.jpg");
assert.deepStrictEqual(f("https://sitio.cl/x.png"), ["https://sitio.cl/x.png"]);
assert.deepStrictEqual(f("data:image/png;base64,AA"), []);
const {imageCandidates: pick, imageFileName: name, largestSrcset} = require("../extension/images.js");
const base = "https://galeria.cl/muestras/";
const googleThumb = "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSAMPLE";
assert.deepStrictEqual(pick({base: "https://www.google.com/search?tbm=isch", src: googleThumb,
  link: "https://photos.example.org/landscape/original.jpeg", srcset: [], data: []}, googleThumb),
  ["https://photos.example.org/landscape/original.jpeg", googleThumb]);
const instaThumb = "https://instagram.test/low.jpg";
assert.strictEqual(pick({base: "https://www.instagram.com/p/ABC/", src: instaThumb,
  srcset: ["https://instagram.test/medium.jpg 640w, https://instagram.test/original.jpg 1440w"], data: []}, instaThumb)[0],
"https://instagram.test/original.jpg");
const tiktokThumb = "https://p16-sign-sg.tiktokcdn.com/low.jpeg";
assert.strictEqual(pick({base: "https://www.tiktok.com/@user/video/123", src: tiktokThumb,
  srcset: ["https://p16-sign-sg.tiktokcdn.com/low.jpeg 320w, https://p16-sign-sg.tiktokcdn.com/full.jpeg 1080w"], data: []}, tiktokThumb)[0],
  "https://p16-sign-sg.tiktokcdn.com/full.jpeg");

assert.deepStrictEqual(pick({base, src: base + "t/1.jpg", srcset: ["t/1.jpg 300w, g/1-2x.jpg 1200w, g/1-1x.jpg 600w"],
  link: base + "full/1.png", data: ["full/1.webp"]}, base + "t/1.jpg"), [
  base + "full/1.png", base + "g/1-2x.jpg", base + "full/1.webp", base + "t/1.jpg"]);
assert.strictEqual(pick({base, src: base + "t/1.jpg", srcset: ["mobile.jpg 600w", "large.jpg 1600w"], data: []}, base + "t/1.jpg")[0],
  base + "large.jpg");
assert.strictEqual(pick({base, src: base + "t/1.jpg", srcset: [], og: base + "full/1.jpg", data: []}, base + "t/1.jpg")[0],
  base + "full/1.jpg");
assert.strictEqual(pick({base, src: "https://instagram.test/t.jpg", srcset: ["https://instagram.test/m.jpg 320w, https://instagram.test/o.jpg 1440w"], data: []}, "https://instagram.test/t.jpg")[0],
  "https://instagram.test/o.jpg");
const wiki = "https://thumb.wikimedia.org/wikipedia/commons/thumb/2/25/Siam.jpg/250px-Siam.jpg";
assert.deepStrictEqual(pick({base: "https://en.wikipedia.org/wiki/Cat", src: wiki, srcset: [wiki + " 1x, https://thumb.wikimedia.org/wikipedia/commons/thumb/2/25/Siam.jpg/330px-Siam.jpg 1.5x"], link: "", data: []}, wiki),
  ["https://upload.wikimedia.org/wikipedia/commons/2/25/Siam.jpg", "https://thumb.wikimedia.org/wikipedia/commons/thumb/2/25/Siam.jpg/330px-Siam.jpg", wiki]);
assert.deepStrictEqual(pick({base, src: base + "t/1.jpg", srcset: [], link: base + "foto/1", data: []}, base + "t/1.jpg"),
  [base + "foto/1", base + "t/1.jpg"]);
assert.deepStrictEqual(pick({base, src: "", srcset: [], link: "", data: ["data:image/png;base64,AA", "/o/x.jpg"]}, ""),
  ["https://galeria.cl/o/x.jpg"]);
assert.deepStrictEqual(pick(null, "https://pbs.twimg.com/media/Ab.jpg?name=small")[0], "https://pbs.twimg.com/media/Ab?format=jpg&name=orig");
assert.strictEqual(largestSrcset("a.jpg 1x, b.jpg 2x", base), base + "b.jpg");
assert.strictEqual(largestSrcset("https://cdn.cl/a,b.jpg 320w, https://cdn.cl/c.jpg 640w", base), "https://cdn.cl/c.jpg");
assert.strictEqual(largestSrcset("", base), "");
assert.strictEqual(largestSrcset("https://cdn.test/a,b.jpg 2x, https://cdn.test/c.jpg 1x", base), "https://cdn.test/a,b.jpg");
assert.strictEqual(largestSrcset("wide.jpg 1800w, tiny.jpg 2x", base), base + "wide.jpg");
assert.strictEqual(largestSrcset("a.jpg, b.jpg 2x", base), base + "b.jpg");
assert.strictEqual(largestSrcset("a.jpg 1x,b.jpg 3x", base), base + "b.jpg");
assert.strictEqual(largestSrcset("only.jpg", base), base + "only.jpg");

assert.deepStrictEqual(pick({base, srcset: [], link: "", data: ["true", "1", "a b"]}, base + "shown.jpg"), [base + "shown.jpg"]);
const hostile = {base, srcset: ["http://127.0.0.1:8080/x.jpg 2x"], link: "http://192.168.1.1/a.jpg",
  data: ["http://localhost/p.png", "http://user:pw@cdn.test/o.png", "http://[::1]/z.png", "http://10.0.0.5/q.png", "file:///etc/passwd", "javascript:alert(1)"]};
assert.deepStrictEqual(pick(hostile, base + "shown.jpg"), [base + "shown.jpg"]);
assert.deepStrictEqual(pick({base: "http://192.168.1.5/g/", srcset: [], link: "http://192.168.1.5/g/o.jpg", data: []}, "http://192.168.1.5/g/t.jpg"),
  ["http://192.168.1.5/g/o.jpg", "http://192.168.1.5/g/t.jpg"]);
const many = {base, srcset: Array.from({length: 30}, (_, i) => `https://cdn.test/${i}.jpg ${i + 1}w`), link: "", data: []};
assert.ok(pick(many, base + "s.jpg").length <= 7);
const {privateHost: priv} = require("../extension/images.js");
for (const h of ["localhost", "a.localhost", "printer.local", "intranet", "127.0.0.1", "10.1.2.3", "172.16.0.1", "172.31.9.9",
  "192.168.0.1", "169.254.1.1", "0.0.0.0", "[::1]", "fd00::1", "fe80::1", "2130706433"]) assert.ok(priv(h), h);
for (const h of ["example.com", "cdn.test", "8.8.8.8", "172.32.0.1", "172.15.0.1", "[2606:4700::1]"]) assert.ok(!priv(h), h);

assert.strictEqual(name("https://x.cl/media/abc", "image/png"), "abc.png");
assert.strictEqual(name("https://x.cl/a/photo.jpg", "image/jpeg; charset=binary"), "photo.jpg");
assert.strictEqual(name("https://x.cl/a/photo.jpeg", "image/jpeg"), "photo.jpg");
assert.strictEqual(name("https://x.cl/a/logo.png", "image/svg+xml"), "logo.svg");
assert.strictEqual(name("https://x.cl/a/anim.webp", "image/gif"), "anim.gif");
assert.strictEqual(name("https://x.cl/a/we%3Ard%2Fname", "image/webp"), "we rd - name.webp");
assert.strictEqual(name("https://x.cl/a/COM1.png", "image/png"), "_COM1.png");
assert.strictEqual(name("https://x.cl/a/ugly123.png", "image/png", "Moon over café. "), "Moon over café.png");
assert.strictEqual(name("https://cdn.test/a/1234567890abcdef1234567890abcdef.jpg", "image/jpeg", "image", "Moon over café | YouTube"), "Moon over café.jpg");
assert.match(name("https://cdn.test/a/1234567890abcdef1234567890abcdef.jpg", "image/jpeg"), /^Cdn \d{4}-\d{2}-\d{2}\.jpg$/);
assert.strictEqual(name("https://x.test/a/p.png", "image/png", 'Night: "Blue" / Gold'), "Night - 'Blue' - Gold.png");
assert.strictEqual(name("https://x.test/a/p.png", "image/png", "A beautiful sunset over the ocean and mountains", ""), "A beautiful sunset over the ocean and mountains.png");
assert.strictEqual(name("https://x.cl/a/p.jpg", "text/html"), "");
assert.strictEqual(name("https://x.cl/a/p.jpg", ""), "p.jpg");
assert.strictEqual(name("https://x.cl/a/p", ""), "");
assert.strictEqual(name("https://media.giphy.com/media/AbC123/giphy.mp4", "video/mp4"), "giphy.mp4");
assert.strictEqual(name("https://pbs.twimg.com/media/Ab.jpg", "image/jpeg", "A fox runs. More details https://t.co/abc", "Post on X"), "A fox runs.jpg");
console.log("images.test.js OK");
