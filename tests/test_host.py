import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
os.environ["TOMO_DATA"] = tempfile.mkdtemp(prefix="vct-test-")
spec = importlib.util.spec_from_file_location("tomo_host", ROOT / "backend" / "host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

VTT = """WEBVTT

00:00:00.000 --> 00:00:02.000
Hola ¿cómo estás?

00:00:02.000 --> 00:00:04.000
Hola ¿cómo estás? muy bien.

00:00:04.000 --> 00:00:06.000
muy bien. Gracias.
"""


class TestImageDownload(unittest.TestCase):
    def test_bounded_image_download_keeps_original_and_rejects_huge_dimensions(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 2, 3) + b"\x00" * 10
        with tempfile.TemporaryDirectory() as directory:
            def response(data, length=None):
                stream = io.BytesIO(data)
                stream.status = 200
                stream.headers = {"Content-Type": "image/png", "Content-Length": str(length or len(data))}
                return stream

            with patch.object(host, "downloads_folder", return_value=Path(directory)), \
                    patch.object(host.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = response(png)
                result = host.job_save_image({"url": "https://example.org/image", "filename": "original.png"}, Mock())
                self.assertEqual(Path(result["path"]).read_bytes(), png)
                opener.return_value.open.return_value = response(png)
                nested = host.job_save_image({"url": "https://example.org/image", "filename": "nested.png", "subfolder": "Tomo/../Images"}, Mock())
                self.assertEqual(Path(nested["path"]).parent, Path(directory) / "Tomo" / "Images")
                opener.return_value.open.return_value = response(png, host.IMAGE_MAX_BYTES + 1)
                with self.assertRaises(ValueError):
                    host.job_save_image({"url": "https://example.org/image", "filename": "huge.png"}, Mock())
                huge = png[:16] + struct.pack(">II", 100_000, 100_000) + png[24:]
                opener.return_value.open.return_value = response(huge)
                with self.assertRaises(ValueError):
                    host.job_save_image({"url": "https://example.org/image", "filename": "huge.png"}, Mock())
                self.assertFalse((Path(directory) / "huge.png").exists())


def native(request: dict, keep_open: bool = False) -> list[dict]:
    req = json.dumps(request).encode()
    proc = subprocess.Popen([sys.executable, str(ROOT / "backend" / "host.py")], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ)
    proc.stdin.write(struct.pack("<I", len(req)) + req)
    proc.stdin.flush()
    if not keep_open:
        proc.stdin.close()
        proc.stdin = None
    if keep_open:
        try:
            out = proc.stdout.read()
            proc.wait(timeout=60)
        finally:
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
    else:
        out, _ = proc.communicate(timeout=60)
    stream, messages = io.BytesIO(out), []
    while header := stream.read(4):
        messages.append(json.loads(stream.read(struct.unpack("<I", header)[0])))
    return messages


class TestDownloadProgress(unittest.TestCase):
    def test_finished_download_clears_stale_percentage_before_postprocessing(self):
        progress = Mock()
        hooks, postprocessor_hooks = host.download_hooks(progress)
        hooks[0]({"status": "downloading", "downloaded_bytes": 71, "total_bytes": 100})
        self.assertEqual(progress.call_args.args[:2], ("download", 71))
        hooks[0]({"status": "finished"})
        self.assertEqual(progress.call_args.args[:2], ("download", None))
        self.assertTrue(progress.call_args.kwargs["force"])
        postprocessor_hooks[0]({"status": "started", "postprocessor": "Merger"})
        self.assertEqual(progress.call_args.args[:2], ("postprocess", None))


class TestMetadata(unittest.TestCase):
    def test_yt_dlp_metadata_fields_and_id3v23_are_preserved(self):
        from yt_dlp.postprocessor.ffmpeg import FFmpegMetadataPP

        with host.make_ydl(["--embed-metadata", "--embed-thumbnail", "--parse-metadata",
                            "%(webpage_url)s:%(meta_comment)s", "--postprocessor-args",
                            "FFmpegMetadata+ffmpeg:-id3v2_version 3"]) as ydl:
            args = ydl.params["postprocessor_args"]
            self.assertEqual(args["ffmpegmetadata+ffmpeg"], ["-id3v2_version", "3"])
            self.assertIn("ffmpeg_i", args)
            steps = ydl.params["postprocessors"]
            self.assertTrue(any(step["key"] == "EmbedThumbnail" for step in steps))
            self.assertTrue(any(step["key"] == "FFmpegMetadata" for step in steps))
            info = {"title": "Sample", "uploader": "Alice", "album": "Series", "upload_date": "20251106",
                    "track_number": 2, "webpage_url": "https://example.com/watch/1"}
            values = [value for key, value in FFmpegMetadataPP(ydl)._get_metadata_opts(info) if key == "-metadata"]
            for field in ("title=Sample", "artist=Alice", "album=Series", "date=20251106",
                          "track=2", "comment=https://example.com/watch/1"):
                self.assertIn(field, values)


class TestText(unittest.TestCase):
    def test_clean_rolling_subtitles(self):
        merged = host.words_merge(host.parse_cues(VTT))
        self.assertEqual(host.render_text(merged), "Hola ¿cómo estás? muy bien. Gracias.")

    def test_timestamps(self):
        text = host.render_timed(host.words_merge(host.parse_cues(VTT)))
        self.assertIn("[00:00:00] Hola ¿cómo estás?", text)
        self.assertIn("[00:00:04] Gracias.", text)

    def test_srt_parse(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\n<v speaker>Buenos días</v>\n"
        self.assertEqual(host.parse_cues(srt), [(1.0, 2.0, "Buenos días")])

    def test_long_hours_timestamp(self):
        cues = host.parse_cues("WEBVTT\n\n1:02:03.500 --> 1:02:05.000\nfinal\n")
        self.assertEqual(cues, [(3723.5, 3725.0, "final")])
        self.assertEqual(host.render_timed(cues), "[01:02:03] final")

    def test_render_srt_vtt(self):
        cues = [(0.0, 3.0, "uno"), (2.0, 4.0, "dos")]
        srt = host.render_srt(cues)
        self.assertIn("1\n00:00:00,000 --> 00:00:02,000\nuno", srt)
        self.assertIn("2\n00:00:02,000 --> 00:00:04,000\ndos", srt)
        self.assertTrue(host.render_vtt(cues).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nuno"))

    def test_paragraphs_do_not_split_every_sentence(self):
        cues = [(i, i + 1, f"Frase número {i}.") for i in range(200)]
        text = host.render_text(cues)
        paragraphs = text.split("\n\n")
        self.assertGreater(len(paragraphs), 3)
        self.assertTrue(all(len(p) >= 500 for p in paragraphs[:-1]))
        self.assertEqual(text.replace("\n\n", " "), " ".join(c[2] for c in cues))

    def test_busy_clipboard_falls_back_to_txt(self):
        info = {"title": "Video", "id": "abc", "subtitles": {"es": [{"ext": "vtt"}]}}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(host, "resolve_target", lambda req, p: (info, "https://a.tld/v", "")), \
                patch.object(host, "subtitle_cues", lambda *a: [(0.0, 1.0, "hola mundo")]), \
                patch.object(host, "clipboard_set", side_effect=host.ClipboardBusy("ocupado")), \
                patch.object(host, "downloads_folder", lambda: Path(tmp)):
            result = host.job_transcript({"url": "https://a.tld/v", "page_url": "", "src_url": ""}, lambda *a, **k: None)
            self.assertIn("locking", result["message"])
            self.assertEqual(Path(result["path"]).read_text(encoding="utf-8").strip(), "hola mundo")

    def test_unique_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.mp4"
            path.write_bytes(b"x")
            self.assertEqual(host.unique_destination(path).name, "example (1).mp4")

    def test_choose_subs_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "sub.en.vtt").write_text("en")
            (directory / "sub.es.vtt").write_text("es")
            self.assertEqual(host.best_subtitle(directory).name, "sub.es.vtt")


YT_LIKE = {
    "subtitles": {"de": [{"ext": "vtt", "name": "German"}], "live_chat": [{"ext": "json"}]},
    "automatic_captions": {"en": [{"ext": "vtt", "name": "English"}], "es-en": [{"ext": "vtt"}],
                           "zh-Hans-en": [{"ext": "vtt"}], "en-en": [{"ext": "vtt"}]},
}


class TestTracks(unittest.TestCase):
    def test_translations_excluded_and_original_detected(self):
        manual, auto, original = host.subtitle_tracks(YT_LIKE)
        self.assertEqual(set(manual), {"de"})
        self.assertEqual(set(auto), {"en"})
        self.assertEqual(original, "en")

    def test_manual_first_then_original_auto(self):
        info = dict(YT_LIKE, subtitles={"en-US": [{"ext": "vtt"}]})
        self.assertEqual(host.pick_track(info, "", ["es", "en"]), ("en-US", "manual"))
        self.assertEqual(host.pick_track(YT_LIKE, "", ["es"]), ("en", "auto"))

    def test_requested_language(self):
        self.assertEqual(host.pick_track(YT_LIKE, "de", []), ("de", "manual"))
        self.assertIsNone(host.pick_track(YT_LIKE, "fr", []))

    def test_original_from_translation_sources(self):
        info = {"automatic_captions": {"en": [{}], "de": [{}], "es-en": [{}], "fr-en": [{}], "es-de": [{}],
                                       "it-en": [{}]}}
        self.assertEqual(host.subtitle_tracks(info)[2], "en")
        self.assertEqual(host.pick_track(info, "", []), ("en", "auto"))

    def test_orig_suffix_preferred(self):
        info = {"automatic_captions": {"en-orig": [{"ext": "vtt"}], "en": [{"ext": "vtt"}]}}
        self.assertEqual(host.pick_track(info, "", []), ("en-orig", "auto"))

    def test_summarize_real_attributes_only(self):
        info = {"id": "x", "title": "T", "formats": [
            {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "fps": 30, "tbr": 4000,
             "ext": "mp4", "filesize": 1000},
            {"format_id": "248", "vcodec": "vp9", "acodec": "none", "height": 1080, "fps": 30, "tbr": 3000, "ext": "webm"},
            {"format_id": "337", "vcodec": "vp09.02", "acodec": "none", "height": 2160, "fps": 60, "tbr": 20000,
             "ext": "webm", "dynamic_range": "HDR10"},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129.5, "ext": "m4a"},
            {"format_id": "sb0", "vcodec": "none", "acodec": "none", "ext": "mhtml"}]}
        s = host.summarize(info)
        self.assertEqual([v["label"] for v in s["videos"]], ["2160p60 HDR", "1080p", "1080p"])
        self.assertEqual(s["videos"][1]["vcodec"], "H.264")
        self.assertIsNone(s["videos"][2]["size"])
        self.assertEqual(s["audios"][0]["id"], "140")
        self.assertTrue(s["has_webm"])

    def test_original_audio_language_is_reported_once(self):
        def audio(fid, lang, preference):
            return {"format_id": fid, "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128, "ext": "m4a",
                    "language": lang, "language_preference": preference}
        video = {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "ext": "mp4"}
        multi = host.summarize({"id": "x", "formats": [video, audio("a1", "en-US", 10), audio("a2", "es-US", -1)]})
        self.assertEqual(multi["audio_original"], "en-US")
        self.assertEqual(multi["audio_langs"], ["en-US", "es-US"])
        single = host.summarize({"id": "x", "formats": [video, audio("a1", "en-US", 10)]})
        self.assertIsNone(single["audio_original"])
        self.assertEqual(single["audio_langs"], [])


PAGE ="""<video muted><source src="/data/sample/48/f2/48f2d4377356ba0cee1f8d4ee278c5bb_720p.mp4"></video>
<a href="https://static1.site.tld/data/48/f2/48f2d4377356ba0cee1f8d4ee278c5bb.webm">Download</a>
<script>var other = "https:\\/\\/static1.site.tld\\/data\\/aa\\/bb\\/aaaabbbbccccddddeeeeffff00001111.mp4";</script>"""
PROBES = {
    "48f2d4377356ba0cee1f8d4ee278c5bb_720p.mp4": {"vcodec": "h264", "acodec": "aac", "height": 810, "width": 1440,
                                                  "fps": 30, "filesize": 5_900_000, "duration": 41.3},
    "48f2d4377356ba0cee1f8d4ee278c5bb.webm": {"vcodec": "vp9", "acodec": "opus", "height": 1440, "width": 2560,
                                              "fps": 30, "filesize": 99_800_000, "duration": 41.3},
    "aaaabbbbccccddddeeeeffff00001111.mp4": {"vcodec": "h264", "acodec": "aac", "height": 1080, "width": 1920,
                                             "fps": 30, "filesize": 50_000_000, "duration": 10.0},
}


class TestVariants(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(host, "fetch_text", lambda url, ref: PAGE),
                        patch.object(host, "probe_media", lambda url, ref: PROBES.get(url.rsplit("/", 1)[-1])),
                        patch.object(host, "resolve_addresses", lambda name: ["93.184.216.34"])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_media_token_links_sample_and_original(self):
        self.assertEqual(host.media_token("https://x/sample/48f2d4377356ba0cee1f8d4ee278c5bb_720p.mp4"),
                         host.media_token("https://y/48f2d4377356ba0cee1f8d4ee278c5bb.webm"))
        self.assertEqual(host.media_token("https://x/clip_1080p.mp4"), host.media_token("https://x/clip-720.webm"))

    def test_media_links_absolute_relative_and_json_escaped(self):
        links = host.media_links(PAGE, "https://site.tld/posts/1")
        self.assertIn("https://site.tld/data/sample/48/f2/48f2d4377356ba0cee1f8d4ee278c5bb_720p.mp4", links)
        self.assertIn("https://static1.site.tld/data/48/f2/48f2d4377356ba0cee1f8d4ee278c5bb.webm", links)
        self.assertIn("https://static1.site.tld/data/aa/bb/aaaabbbbccccddddeeeeffff00001111.mp4", links)

    def test_clicked_video_gets_original_quality_only_from_its_variants(self):
        src = "https://site.tld/data/sample/48/f2/48f2d4377356ba0cee1f8d4ee278c5bb_720p.mp4"
        info = host.best_variants(None, "https://site.tld/posts/1", "", src, fresh=True)
        self.assertEqual(info["_type"], "video")
        self.assertEqual(sorted(f["height"] for f in info["formats"]), [810, 1440])
        self.assertTrue(all(f["acodec"] != "none" for f in info["formats"]))

    def test_page_with_two_videos_becomes_choice(self):
        info = host.best_variants(None, "https://site.tld/posts/2", "", "", fresh=True)
        self.assertEqual(info["_type"], "playlist")
        self.assertEqual(len(info["entries"]), 2)
        self.assertTrue(any(e["url"].endswith(".webm") for e in info["entries"]))


class TestSecurity(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(host, "resolve_addresses", return_value=["93.184.216.34"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_name_resolving_to_local_address_rejected(self):
        for address in ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fd00::1"]:
            with self.subTest(address=address), patch.object(host, "resolve_addresses", return_value=[address]):
                with self.assertRaises(ValueError):
                    host.validate_url("https://innocent.example.com/a.mp4")

    def test_redirect_to_local_address_is_discarded(self):
        def serve(handler):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            return server

        class Final(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"payload")

            def log_message(self, *args):
                pass

        final = serve(Final)

        class Redirect(Final):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{final.server_port}/end")
                self.end_headers()

        start = f"http://127.0.0.1:{serve(Redirect).server_port}/start"
        with host.make_ydl([]) as ydl:
            with self.assertRaises(ValueError) as caught:
                ydl.urlopen(start)
        self.assertIn("Local network", str(caught.exception))
        with patch.object(host, "is_internal_host", return_value=False), host.make_ydl([]) as ydl:
            self.assertEqual(ydl.urlopen(start).read(), b"payload")

    def test_duration_limit(self):
        host.check_limits({"duration": host.MAX_DURATION})
        host.check_limits({})
        with self.assertRaises(RuntimeError):
            host.check_limits({"duration": host.MAX_DURATION + 1})

    def test_hostile_requests_are_rejected_cleanly(self):
        hostile = [None, 1, [], {}, ["download"], "", "x" * 5000, "../../etc/passwd", "download\x00", True]
        for value in hostile:
            with self.subTest(action=value), self.assertRaises(ValueError):
                host.normalize({"action": value})
        for field, value in [("url", None), ("url", 5), ("url", ["https://a.tld"]), ("url", {"a": 1}),
                             ("page_url", 7), ("src_url", ["x"])]:
            request = {"action": "download", "url": "https://example.com/v", field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                host.normalize(request)

    def test_oversized_and_truncated_messages_rejected(self):
        for raw in [struct.pack("<I", host.MAX_MESSAGE + 1) + b"{}", struct.pack("<I", 0), struct.pack("<I", 50) + b"{}",
                    b"\x01\x00", struct.pack("<I", 4) + b"nope", struct.pack("<I", 2) + b"[]"]:
            with self.subTest(raw=raw[:12]), self.assertRaises(ValueError):
                host.read_message(io.BytesIO(raw))

    def test_session_cookies_are_scoped(self):
        raw = "\n".join([
            ".reddit.com\tTRUE\t/\tTRUE\t0\tsession\tabc",
            "#HttpOnly_www.reddit.com\tFALSE\t/\tTRUE\t1893456000\ttoken\txyz",
            ".other.com\tTRUE\t/\tTRUE\t0\tleak\tno",
            "notreddit.com\tFALSE\t/\tFALSE\t0\tleak\tno",
            "reddit.com\tbad line",
        ])
        out = host.scoped_cookies(raw, ["https://www.reddit.com/r/x", ""])
        self.assertIn("session\tabc", out)
        self.assertIn("token\txyz", out)
        self.assertNotIn("leak", out)
        self.assertEqual(host.scoped_cookies(raw, ["https://example.org/"]), "")
        self.assertEqual(host.scoped_cookies(None, ["https://www.reddit.com/"]), "")

    def test_manifest_detection(self):
        self.assertTrue(host.is_manifest({"formats": [{"protocol": "m3u8_native"}]}))
        self.assertTrue(host.is_manifest({"formats": [{"protocol": "http_dash_segments"}]}))
        self.assertFalse(host.is_manifest({"formats": [{"protocol": "https"}]}))
        self.assertFalse(host.is_manifest({}))

    def test_chromium_client_refuses_youtube(self):
        urls = ("https://www.youtube.com/watch?v=x", "https://youtu.be/x", "https://music.youtube.com/watch?v=x",
                "https://www.youtube-nocookie.com/embed/x")
        with patch.object(host, "CHROMIUM_CLIENT", True):
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    host.validate_url(url)
            self.assertEqual(host.validate_url("https://example.com/a.mp4"), "https://example.com/a.mp4")
        with patch.object(host, "CHROMIUM_CLIENT", False), patch.object(host, "resolve_addresses", return_value=["93.184.216.34"]):
            self.assertEqual(host.validate_url(urls[1]), urls[1])
        with patch.object(host, "CHROMIUM_CLIENT", False), patch.object(host, "resolve_addresses", return_value=[]):
            with self.assertRaises(ValueError):
                host.validate_url(urls[1])

    def test_validate_url(self):
        for url in ["file:///tmp/a.mp4", "blob:https://x/y", "javascript:alert(1)", "http://u:p@site.tld/video",
                    "https://a.tld/v\r\nX-Injected: 1", "https://a.tld/ v", "ftp://a.tld/v.mp4", "https:///nohost"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                host.validate_url(url)
        self.assertEqual(host.validate_url("https://example.com/video.webm"), "https://example.com/video.webm")
        self.assertEqual(host.validate_url("https://vimeo.com/22439234?x=1"), "https://player.vimeo.com/video/22439234")
        self.assertEqual(host.validate_url("https://www.vimeo.com/22439234/0123456789"),
                         "https://player.vimeo.com/video/22439234?h=0123456789")
        self.assertEqual(host.validate_url("https://vimeo.com/channels/staffpicks/1"), "https://vimeo.com/channels/staffpicks/1")

    def test_internal_addresses_rejected(self):
        for url in ["http://localhost:8080/a.mp4", "http://127.0.0.1/a.mp4", "http://192.168.1.10/a.mp4",
                    "http://10.0.0.5/a.mp4", "http://169.254.169.254/latest/meta-data", "http://[::1]/a.mp4",
                    "http://0.0.0.0/a.mp4", "http://nas.local/a.mp4", "http://intranet/a.mp4",
                    "http://router.lan/a.mp4", "http://localhost./a.mp4"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                host.validate_url(url)
        for url in ["https://8.8.8.8/a.mp4", "https://cdn.example.com:8443/a.mp4", "https://[2606:4700::1111]/a.mp4"]:
            with self.subTest(url=url):
                self.assertEqual(host.validate_url(url), url)

    def test_format_spec_injection_rejected(self):
        for bad in ["137/best", "bv*+ba", "137,140", "x[height>1]", "a b", "-o", "../x"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                host.preset_args({"preset": "custom", "video_id": bad})
        self.assertEqual(host.preset_args({"preset": "custom", "video_id": "hls-1080p", "audio_id": "140",
                                           "container": "mkv"})[0][:2], ["-f", "hls-1080p+140"])
        with self.assertRaises(ValueError):
            host.preset_args({"preset": "custom", "video_id": "137", "container": "exe"})

    def test_quality_by_resolution_is_built_from_validated_numbers(self):
        args, compatible = host.preset_args({"preset": "custom", "height": 1080, "fps60": True, "hdr": False,
                                             "container": "mp4", "codec": "auto", "audio_lang": "es"})
        spec = args[1]
        self.assertTrue(spec.startswith("bv*[height=1080][fps>30][dynamic_range=?SDR]+ba[language^=es]"))
        self.assertIn("/bv*[height<=?1080]+ba/b[height<=?1080]", spec)
        self.assertIn("vcodec:h264,acodec:aac", args)
        self.assertTrue(compatible)
        self.assertIn("vcodec:av01", host.preset_args({"preset": "custom", "height": 2160, "codec": "av1"})[0])
        for bad in ["1080", True, 0, 99999, 1080.5, None]:
            with self.subTest(height=bad), self.assertRaises(ValueError):
                host.preset_args({"preset": "custom", "height": bad})
        with self.assertRaises(ValueError):
            host.preset_args({"preset": "custom", "height": 720, "codec": "h265;rm"})
        with self.assertRaises(ValueError):
            host.preset_args({"preset": "audio", "audio_lang": "es]+b"})

    def test_audio_presets(self):
        self.assertEqual(host.preset_args({"preset": "m4a"})[0][:2], ["-f", "ba[acodec^=mp4a]/ba[ext=m4a]/ba/b"])
        self.assertEqual(host.preset_args({"preset": "mp3", "audio_lang": "en"})[0][1], "ba[language^=en]/ba/b")

    def test_parameters_whitelisted(self):
        for req in [{"action": "rm"}, {"action": "download", "url": "file:///c:/x"},
                    {"action": "transcript", "url": "https://a.tld/v", "src_url": "javascript:x"}]:
            with self.subTest(req=req), self.assertRaises(ValueError):
                host.normalize(req)
        with self.assertRaises(ValueError):
            host.lang_code("es;rm")
        with self.assertRaises(ValueError):
            host.choice("cuda", host.ASR_MODELS, "small")

    def test_open_only_files_created_by_host(self):
        with self.assertRaises(ValueError):
            host.allowed_output(r"C:\Windows\System32\calc.exe")
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "x.mp4"
            outside.write_bytes(b"x")
            host.record_history(outside)
            with self.assertRaises(ValueError):
                host.allowed_output(str(outside))

    def test_native_open_and_reveal_reject_untracked_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as tmp:
            path = Path(tmp) / "vídeo final.txt"
            path.write_text("test", encoding="utf-8")
            with patch.dict(os.environ, {"TOMO_DATA": tmp}):
                for action in ("open", "reveal"):
                    with self.subTest(action=action):
                        messages = native({"action": action, "path": str(path)}, keep_open=True)
                        self.assertEqual(messages[-1]["status"], "error")
                        self.assertTrue(messages[-1]["message"])

    def test_redact_hides_urls(self):
        text = host.redact("ERROR: [youtube] abc: fallo en https://x.tld/v?token=SECRETO")
        self.assertNotIn("SECRETO", text)
        self.assertIn("[URL ocultada]", text)

    def test_legacy_actions_mapped(self):
        action, req = host.normalize({"action": "captions-timed", "url": "https://a.tld/v"})
        self.assertEqual((action, req["source"], req["timed"]), ("transcript", "subs", True))

    def test_native_protocol_invalid_action(self):
        messages = native({"action": "arbitrary", "url": "https://example.com"})
        self.assertEqual([m["status"] for m in messages], ["started", "error"])

    def test_native_ping(self):
        messages = native({"action": "ping"})
        self.assertEqual(messages[-1]["status"], "done")
        self.assertEqual(messages[-1]["protocol"], host.PROTOCOL)


def make_sample(path: Path, vcodec: str | None, acodec: str | None, seconds: int = 1) -> None:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if vcodec:
        args += ["-f", "lavfi", "-i", "color=c=black:s=160x90:r=10"]
    if acodec:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    args += ["-t", str(seconds)] + (["-c:v", vcodec] if vcodec else [])
    if acodec:
        args += ["-c:a", acodec]
    subprocess.run([*args, str(path)], check=True, capture_output=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg no disponible")
class TestConvert(unittest.TestCase):
    def setUp(self):
        host.CANCEL.clear()

    def test_vp9_opus_webm_to_compatible_mp4(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "video.webm"
            make_sample(sample, "libvpx-vp9", "libopus")
            result, note = host.ensure_compatible_mp4(sample, Path(tmp), lambda *a, **k: None)
            self.assertEqual(result.name, "video.mp4")
            self.assertEqual(host.ffprobe_codecs(result), ("h264", "aac", "yuv420p"))
            self.assertIn("re-encoded", note)
            types = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                                    str(result)], capture_output=True, text=True).stdout.split()
            self.assertEqual(types, ["video", "audio"])

    def test_compatible_mp4_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "video.mp4"
            make_sample(sample, "libx264", "aac")
            result, note = host.ensure_compatible_mp4(sample, Path(tmp), lambda *a, **k: None)
            self.assertEqual((result, note), (sample, "no re-encoding"))

    def test_h264_in_mkv_is_remuxed_not_reencoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "video.mkv"
            make_sample(sample, "libx264", "libopus")
            result, note = host.ensure_compatible_mp4(sample, Path(tmp), lambda *a, **k: None)
            self.assertEqual(host.ffprobe_codecs(result)[:2], ("h264", "aac"))
            self.assertEqual(note, "audio converted to AAC")

    def test_ffmpeg_cancel_kills_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "long.webm"
            make_sample(sample, "libvpx-vp9", None, seconds=60)
            calls = []
            def progress(*args, **kwargs):
                calls.append(args)
                host.CANCEL.set()
                host.check_cancel()
            start = time.monotonic()
            with self.assertRaises(host.Cancelled):
                host.run_ffmpeg(["-i", str(sample), "-c:v", "libx264", "-preset", "veryslow", str(Path(tmp) / "o.mp4")],
                                60.0, progress, "convert", "x")
            self.assertLess(time.monotonic() - start, 20)
            host.CANCEL.clear()

    def test_cover_embedded_per_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            cover = Path(tmp) / "cover.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x36", "-frames:v", "1",
                            str(cover)], check=True)
            for name, vcodec, acodec in [("v.mp4", "libx264", "aac"), ("v.mkv", "libx264", "libopus"),
                                         ("a.mp3", None, "libmp3lame"), ("v.webm", "libvpx-vp9", "libopus")]:
                sample = Path(tmp) / name
                make_sample(sample, vcodec, acodec)
                host.embed_cover(sample, cover)
                out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                                      str(sample)], capture_output=True, text=True).stdout
                self.assertEqual("mjpeg" in out, name != "v.webm", name)


class TestLangTags(unittest.TestCase):
    def test_youtube_keys_normalized(self):
        cases = {"es-ES-7eCR4kqQbL4": "es-ES", "en-nP7-2PuUl7o": "en", "es-orig": "es", "pt-BR": "pt-BR",
                 "zh-Hans": "zh-Hans", "es-419": "es-419", "fil": "fil", "en": "en"}
        for key, tag in cases.items():
            self.assertEqual(host.lang_tag(key), tag, key)

    def test_orig_drops_machine_translations_and_duplicates(self):
        track = [{"ext": "vtt", "url": "https://x/y"}]
        info = {"subtitles": {"es-ES-7eCR4kqQbL4": track, "es": track, "en-nP7-2PuUl7o": track},
                "automatic_captions": {"es-orig": track, "es": track, "en": track, "fr": track}}
        subs = host.summarize(info)["subtitles"]
        self.assertEqual(subs["original"], "es")
        self.assertEqual([l["code"] for l in subs["languages"]], ["es-ES", "es", "en"])
        self.assertEqual(host.pick_track(info, "es-ES", []), ("es-ES-7eCR4kqQbL4", "manual"))
        _, auto, _ = host.subtitle_tracks(info)
        self.assertNotIn("fr", auto)


class FakeAI(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        auth = self.headers.get("Authorization", "") + self.headers.get("x-api-key", "") + self.headers.get("x-goog-api-key", "")
        if "mala" in auth:
            self.send_response(401); self.end_headers(); return
        if ":generateContent" in self.path:
            prompt = body["contents"][0]["parts"][0]["text"]
            reply = {"candidates": [{"content": {"parts": [{"text": "gemini:" + prompt[:20]}]}}]}
        else:
            prompt = body["messages"][1]["content"]
            if prompt.startswith("["):
                text = json.dumps([s.upper() for s in json.loads(prompt)])
            else:
                text = "openai:" + prompt[:20]
            reply = {"choices": [{"message": {"content": text}}]}
        data = json.dumps(reply).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *args):
        pass


class TestAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ai = host.load_ai()
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), FakeAI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def cfg(self, provider, key="buena"):
        return self.ai.config({"provider": provider, "key": key, "base": self.base, "model": "m-1"}, host.T)

    def test_api_formats(self):
        for provider in ("openai", "gemini"):
            self.assertTrue(self.ai.complete(self.cfg(provider), "sys", "hola", host.T).startswith(provider[:6]), provider)

    def test_translate_keeps_line_count(self):
        lines = [f"línea {i}" for i in range(130)]
        out = self.ai.translate_lines(self.cfg("openai"), lines, "English", host.T)
        self.assertEqual(out, [s.upper() for s in lines])

    def test_bad_key_message_never_contains_key(self):
        with self.assertRaises(self.ai.AIError) as ctx:
            self.ai.complete(self.cfg("openai", key="mala-sk-SECRETO"), "s", "p", host.T)
        self.assertNotIn("SECRETO", str(ctx.exception))

    def test_config_rejects_remote_http_and_missing_key(self):
        with self.assertRaises(self.ai.AIError):
            self.ai.config({"provider": "custom", "key": "k", "base": "http://example.com/v1"}, host.T)
        with self.assertRaises(self.ai.AIError):
            self.ai.config({"provider": "openai", "key": ""}, host.T)


class TestI18n(unittest.TestCase):
    def tearDown(self):
        host.set_lang("en")

    def test_messages_follow_browser_language(self):
        host.set_lang("en-US")
        self.assertEqual(host.T("Analyzing the video"), "Analyzing the video")
        host.set_lang("fr")
        self.assertEqual(host.T("Saved to Downloads: {name}", name="a.mp4"), "Enregistré dans Téléchargements : a.mp4")
        host.set_lang("xx")
        self.assertEqual(host.T("Cancelled."), "Cancelled.")

    def test_former_untranslated_error_and_tool_pattern(self):
        host.set_lang("en")
        self.assertEqual(host.T("Path outside Downloads."), "Path outside Downloads.")
        for lang in ("es", "fr"):
            host.set_lang(lang)
            self.assertEqual(host.T("Path outside Downloads."), "Ruta fuera de Descargas.")
        host.set_lang("en")
        for error in ("Falta una herramienta: ffmpeg.", "A tool is missing: ffmpeg."):
            self.assertEqual(host.friendly(Exception(error)), "FFmpeg is missing. Run the installer again.")

    def test_rendered_messages_match_pre_migration(self):
        reference = json.loads((ROOT / "tests" / "locale_baseline.json").read_text("utf-8"))
        catalog = json.loads((ROOT / "backend" / "locales.json").read_text("utf-8"))
        self.assertEqual(set(reference["keys"]), set(catalog["es"]) - {"Path outside Downloads."})
        for key, expected in reference["keys"].items():
            rendered = []
            for lang in reference["languages"]:
                host.set_lang(lang)
                rendered.append(host.T(key))
            digest = hashlib.sha256(json.dumps(rendered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
            self.assertEqual(digest, expected, key)

    def test_every_language_covers_every_key(self):
        catalog = json.loads((Path(host.__file__).with_name("locales.json")).read_text("utf-8"))
        keys = set(catalog["es"])
        used = {"video re-encoded to H.264 (slower, minimal loss)", "audio converted to AAC",
                "automatic subtitles ({code})"}
        for name in ("host.py", "ai.py"):
            used |= set(re.findall(r'\bT\(\s*"((?:[^"\\]|\\.)*)"', (ROOT / "backend" / name).read_text("utf-8")))
        self.assertEqual(used, keys)
        for lang, table in catalog.items():
            self.assertEqual(set(table), keys, lang)
            for k, v in table.items():
                self.assertEqual(set(re.findall(r"\{(\w+)\}", k)), set(re.findall(r"\{(\w+)\}", v)), f"{lang}: {k}")


class TestAcceptanceFixes(unittest.TestCase):
    def test_site_suffixes_are_stripped_from_titles(self):
        for tail in (" - Reddit", " | Facebook", " - Tumblr", " on Instagram", " - YouTube - YouTube", " | TikTok video"):
            self.assertEqual(host.clean_title("Sunset over the bay" + tail), "Sunset over the bay", tail)
        self.assertEqual(host.clean_title("Kick the habit"), "Kick the habit")

    def test_reddit_errors_get_a_clear_message(self):
        login = host.T("The site requires signing in. Sign in in your browser and turn on “Use my session” in Settings.")
        for text in ("Account authentication is required. Use --cookies-from-browser",
                     "Quarantined subreddit; an account that has opted in is required",
                     "Private subreddit; an account that has been approved is required"):
            self.assertEqual(host.friendly(Exception(text)), login, text)
            self.assertEqual(host.error_kind(Exception(text)), "site", text)
        self.assertEqual(host.friendly(Exception("No media found")), host.T("There's no video on this page."))
        self.assertEqual(host.error_kind(Exception("Your IP address is unable to access the Reddit API")), "site")

    def test_blocked_page_is_retried_with_browser_impersonation(self):
        calls = []

        class Ydl:
            def __init__(self, args):
                self.args = args

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                if "--impersonate" not in self.args:
                    raise Exception("Unable to download webpage: HTTP Error 403: Forbidden")
                return {"id": "x"}

            def sanitize_info(self, info):
                return info

        def make(args, *rest):
            calls.append(args)
            return Ydl(args)

        with patch.object(host, "make_ydl", make), patch.object(host, "can_impersonate", return_value=True), \
                patch.object(host, "cache_json"):
            self.assertEqual(host.resolve_info("https://www.tumblr.com/x/1", fresh=True), {"id": "x"})
        self.assertEqual(len(calls), 2)
        with patch.object(host, "make_ydl", make), patch.object(host, "can_impersonate", return_value=False), \
                patch.object(host, "cache_json"), self.assertRaises(Exception):
            host.resolve_info("https://www.tumblr.com/x/2", fresh=True)

    def test_blocked_extractor_uses_generic_fallback(self):
        calls = []

        class Ydl:
            def __init__(self, args):
                self.args = args

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                calls.append((url, self.args))
                if "--force-generic-extractor" not in self.args:
                    raise Exception("HTTP Error 403: Forbidden")
                return {"id": "tumblr-media", "extractor_key": "Generic"}

            def sanitize_info(self, info):
                return info

        with patch.object(host, "make_ydl", Ydl), patch.object(host, "can_impersonate", return_value=True), \
                patch.object(host, "cache_json"):
            info = host.resolve_info("https://blog.tumblr.com/post/123", fresh=True)
        self.assertEqual(info["id"], "tumblr-media")
        self.assertEqual(len(calls), 3)
        self.assertNotIn("--impersonate", calls[0][1])
        self.assertIn("--impersonate", calls[1][1])
        self.assertIn("--force-generic-extractor", calls[2][1])

    def test_successful_extractor_is_not_retried(self):
        calls = []

        class Ydl:
            def __init__(self, args):
                calls.append(args)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                return {"id": "ok"}

            def sanitize_info(self, info):
                return info

        with patch.object(host, "make_ydl", Ydl), patch.object(host, "can_impersonate", return_value=True), \
                patch.object(host, "cache_json"):
            self.assertEqual(host.resolve_info("https://example.com/video", fresh=True), {"id": "ok"})
        self.assertEqual(len(calls), 1)

    def test_access_restrictions_are_not_retried(self):
        for message in ("HTTP Error 403: Sign in to continue", "HTTP Error 403: DRM protected",
                        "HTTP Error 403: Not available in your country"):
            calls = []

            class Ydl:
                def __init__(self, args):
                    calls.append(args)

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def extract_info(self, url, download=False):
                    raise Exception(message)

                def sanitize_info(self, info):
                    return info

            with self.subTest(message=message), patch.object(host, "make_ydl", Ydl), \
                    patch.object(host, "can_impersonate", return_value=True), self.assertRaisesRegex(Exception, message):
                host.resolve_info("https://example.com/video", fresh=True)
            self.assertEqual(len(calls), 1)

    def test_spanish_sources_use_video_without_accent(self):
        source = (ROOT / "backend" / "host.py").read_text("utf-8")
        catalog = json.loads((ROOT / "backend" / "locales.json").read_text("utf-8"))
        self.assertNotRegex(source, r"[vV]ídeo")
        self.assertFalse([k for table in catalog.values() for k in table if re.search(r"[vV]ídeo", k)])


class TestErrorKind(unittest.TestCase):
    def test_only_site_changes_trigger_self_repair(self):
        kind = lambda text: host.error_kind(Exception(text))
        self.assertEqual(kind("ERROR: [youtube] x: Unable to extract nsig function"), "extractor")
        self.assertEqual(kind("HTTP Error 403: Forbidden"), "extractor")
        self.assertEqual(kind("HTTP Error 404: Not Found"), "site")
        self.assertEqual(kind("Sign in to confirm you're not a bot"), "site")
        self.assertEqual(kind("This video is DRM protected"), "site")
        self.assertEqual(host.error_kind(RuntimeError("mensaje propio")), "user")


class TestNames(unittest.TestCase):
    def test_title_then_author(self):
        self.assertEqual(host.display_name({"title": "Me at the zoo", "uploader": "jawed"}, ".mp4"),
                         "Me at the zoo - jawed.mp4")

    def test_author_prefix_moves_to_end(self):
        info = {"title": "Captain America - Find out?", "uploader": "Captain America"}
        self.assertEqual(host.display_name(info, "mp4"), "Find out - Captain America.mp4")

    def test_no_author_and_unsafe_chars(self):
        self.assertEqual(host.display_name({"title": 'a/b: "c"', "id": "x"}, "mkv"), "a - b - 'c'.mkv")
        self.assertRegex(host.display_name({"id": "123"}, "mp3"), r"^Autor - Sitio \d{4}-\d{2}-\d{2}\.mp3$")
        self.assertEqual(host.display_name({"title": "CON. "}, ".MP4"), "_CON.mp4")
        self.assertEqual(host.display_name({"title": "  Café   Luna. "}, "txt", suffix="resumen"),
                         "Café Luna (resumen).txt")
        self.assertLessEqual(len(host.display_name({"title": "á" * 200}, "mp4").encode("utf-8")), 184)
        self.assertTrue(host.display_name({"title": "A" * 200, "uploader": "Alice"}, "mp4")
                        .endswith(" - Alice.mp4"))

    def test_template_fields_and_empty_pieces(self):
        info = {"title": "Clip", "uploader": "ana", "id": "abc", "upload_date": "20250102", "extractor_key": "Youtube"}
        self.assertEqual(host.display_name(info, "mp4", "{date} {author} - {title} [{id}]"),
                         "2025-01-02 ana - Clip [abc].mp4")
        self.assertEqual(host.display_name({"title": "Clip"}, "mp4", "{title} - {author}"), "Clip.mp4")
        self.assertEqual(host.display_name({"title": "Clip"}, "mp4", "{nope}"), "Clip.mp4")
        self.assertEqual(host.display_name({"id": "abc"}, "mp4", "{title}"), "Video.mp4")
        self.assertEqual(host.display_name({"title": "a/b"}, "mp4", "../{title}"), "a - b.mp4")

    def test_real_world_titles_and_variants(self):
        self.assertEqual(host.display_name({"title": "Sunday drive! #weekend @ana https://t.co/x",
                                            "extractor_key": "Twitter", "uploader": "Ana"}, "mp4"),
                         "Sunday drive - Ana.mp4")
        self.assertEqual(host.display_name({"title": "First dance. More details #fyp",
                                            "extractor_key": "TikTok", "uploader": "Mira"}, "mp4", suffix="720p"),
                         "First dance - Mira (720p).mp4")
        self.assertEqual(host.clean_title("First dance https://t.co/abc. More details", "TikTok"), "First dance")
        self.assertEqual(host.clean_title("Watch this https://t.co/abc! More details", "Twitter"), "Watch this")
        self.assertEqual(host.display_name({"title": "Song | YouTube", "artist": "Luna", "track": "Café / Noche"}, "mp3"),
                         "Luna - Café - Noche.mp3")
        self.assertEqual(host.display_name({"title": "Another week at NASA. More soon", "extractor_key": "TikTok",
                                            "artist": "Luna", "track": "original sound - Luna", "uploader": "mira"}, "mp4"),
                         "Another week at NASA - mira.mp4")
        self.assertEqual(host.display_name({"title": "Another week of building at NASA 🚀 🌔 Artem...", "extractor_key": "TikTok",
                                            "description": "Another week of building at NASA 🚀 🌔 Artemis III core stage integrated",
                                            "uploader": "mira"}, "mp4"),
                         "Another week of building at NASA - mira.mp4")
        self.assertEqual(host.clean_title("Wait for it... 😱"), "Wait for it")
        self.assertNotIn("track", host.without_soundtrack({"extractor_key": "TikTok", "track": "x", "title": "y"}))
        self.assertEqual(host.without_soundtrack({"extractor_key": "Youtube", "track": "x"}), {"extractor_key": "Youtube", "track": "x"})
        self.assertEqual(host.display_name({"title": "Clip - YouTube", "uploader": "Mira"}, "mp4", suffix="audio es"),
                         "Clip - Mira (audio es).mp4")
        self.assertEqual(host.display_name({"uploader": "Mira", "extractor_key": "TikTok",
                                            "upload_date": "20251106", "id": "abc"}, "txt", suffix="transcripción"),
                         "Mira - TikTok 2025-11-06 (transcripción).txt")
        self.assertEqual(host.safe_name("An introduction to the world of videos", 20), "An introduction to")

    def test_subfolder_stays_below_downloads(self):
        self.assertEqual(host.safe_subfolder("Música/Rock"), Path("Música") / "Rock")
        self.assertEqual(host.safe_subfolder("../../etc"), Path("etc"))
        self.assertEqual(host.safe_subfolder("C:\\Windows"), Path("C_") / "Windows")
        self.assertEqual(host.safe_subfolder("/abs//a/b/c/d"), Path("abs") / "a" / "b")
        self.assertEqual(host.safe_subfolder(None), Path())


class TestVerifyOutput(unittest.TestCase):
    def test_empty_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "a.mp4"
            empty.write_bytes(b"")
            with self.assertRaises(RuntimeError):
                host.verify_output(empty)

    def test_garbage_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "a.mp4"
            junk.write_bytes(b"not a video" * 50)
            with patch.object(host, "run", side_effect=RuntimeError("invalid data")):
                with self.assertRaises(RuntimeError):
                    host.verify_output(junk)

    def test_playable_file_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "a.mp4"
            good.write_bytes(b"x")
            out = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
            with patch.object(host, "run", return_value=json.dumps(out)), \
                    patch.object(host, "ffprobe_duration", return_value=3.0):
                host.verify_output(good)
            with patch.object(host, "run", return_value=json.dumps({"streams": [{"codec_type": "video"}]})), \
                    patch.object(host, "ffprobe_duration", return_value=3.0):
                host.verify_output(good)
                with self.assertRaises(RuntimeError):
                    host.verify_output(good, audio_only=True)


if __name__ == "__main__":
    unittest.main()


class HistoryConcurrencyTest(unittest.TestCase):
    def test_parallel_records_are_not_lost(self):
        import subprocess, sys, tempfile
        code = ("import sys, importlib.util, pathlib\n"
                "spec = importlib.util.spec_from_file_location('host', sys.argv[1])\n"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                "m.HISTORY = pathlib.Path(sys.argv[2])\n"
                "for i in range(15): m.record_history(pathlib.Path(sys.argv[3] + str(i)))\n")
        host = str(Path(__file__).resolve().parents[1] / "backend" / "host.py")
        with tempfile.TemporaryDirectory() as tmp:
            history = str(Path(tmp) / "history.json")
            procs = [subprocess.Popen([sys.executable, "-I", "-c", code, host, history, f"{tmp}/p{n}_"])
                     for n in range(4)]
            self.assertTrue(all(p.wait(60) == 0 for p in procs))
            self.assertEqual(len(json.loads(Path(history).read_text("utf-8"))), 60)
