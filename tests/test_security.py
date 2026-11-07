import importlib.util
import io
import json
import os
from pathlib import Path
import select
import socket
import tempfile
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from unittest.mock import Mock, patch
from urllib.response import addinfourl


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tomo_security_host", ROOT / "backend" / "host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
ai = host._sibling("ai")


class SecurityTests(unittest.TestCase):
    def test_oversized_reply_becomes_error(self):
        out = io.BytesIO()
        with patch.object(host, "_OUT", out):
            host.send({"status": "done", "title": "x" * (host.MAX_REPLY + 1)})
        size = int.from_bytes(out.getvalue()[:4], "little")
        self.assertLess(size, 1000)
        self.assertEqual(json.loads(out.getvalue()[4:])["status"], "error")

    def test_job_dirs_are_tracked_until_cleanup(self):
        job = host.job_tmp()
        self.assertIn(job.name, host._JOB_DIRS)
        job.cleanup()
        self.assertNotIn(job.name, host._JOB_DIRS)
        self.assertFalse(Path(job.name).exists())

    def test_clip_limits_strings_only(self):
        self.assertEqual(host.clip("abcdef", 3), "abc")
        self.assertIsNone(host.clip(None, 3))
        self.assertEqual(host.short_url("https://a.test/x"), "https://a.test/x")
        self.assertIsNone(host.short_url("https://a.test/" + "x" * host.MAX_URL))

    def setUp(self):
        parent = ROOT / "backend" / "tmp"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="security-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in {
            "DATA": self.root, "TMP_ROOT": self.root,
            "CACHE": self.root / "cache", "HISTORY": self.root / "history.json",
            "METRICS": self.root / "metrics.jsonl", "AUTHENTICATED": False,
            "RECORD_HISTORY": True, "SESSION_FILE": None,
        }.items():
            self.patch(host, name, value)
        self.patch(host, "downloads_folder", lambda: self.root / "downloads")

    def patch(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def listener(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen()
        self.addCleanup(server.close)
        return server

    def assert_no_connections(self, server):
        connections = 0
        while select.select([server], [], [], 0)[0]:
            connection, _ = server.accept()
            connection.close()
            connections += 1
        self.assertEqual(connections, 0)

    def test_local_transport_connects_zero_times(self):
        server = self.listener()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), host.SafeHTTPHandler())
        with self.assertRaises(ValueError):
            opener.open(f"http://127.0.0.1:{server.getsockname()[1]}/")
        self.assert_no_connections(server)

    def test_public_redirect_to_local_connects_zero_times(self):
        server = self.listener()
        headers = Message()
        headers["Location"] = f"http://127.0.0.1:{server.getsockname()[1]}/private"
        response = addinfourl(io.BytesIO(b""), headers, "http://public.example/start", 302)
        response.msg = "Found"
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), host.SafeHTTPHandler(), host.SafeRedirectHandler())
        with patch.object(host.SafeHTTPHandler, "http_open", return_value=response), \
                patch.object(host, "resolve_addresses", return_value=["93.184.216.34"]):
            with self.assertRaises(ValueError):
                opener.open("http://public.example/start")
        self.assert_no_connections(server)

    def test_mixed_dns_answers_rejected_before_socket_creation(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
                   for ip in ("93.184.216.34", "127.0.0.1")]
        with patch.object(socket, "getaddrinfo", return_value=answers), patch.object(socket, "socket") as create:
            with self.assertRaises(ValueError):
                host.approved_connection(("public.example", 80))
            create.assert_not_called()

    def test_connection_pins_single_resolution(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        sock = Mock()
        with patch.object(socket, "getaddrinfo", side_effect=[answers, AssertionError("second DNS")]) as dns, \
                patch.object(socket, "socket", return_value=sock):
            self.assertIs(host.approved_connection(("public.example", 443), 5), sock)
        dns.assert_called_once()
        sock.connect.assert_called_once_with(("93.184.216.34", 443))

    def test_dns_failure_is_closed(self):
        with patch.object(host, "resolve_addresses", return_value=[]):
            with self.assertRaises(ValueError):
                host.validate_url("https://public.example/video")
        with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror), \
                patch.object(socket, "socket") as create:
            with self.assertRaises(OSError):
                host.approved_connection(("public.example", 80))
            create.assert_not_called()

    def test_remote_probe_never_launches_ffprobe(self):
        server = self.listener()
        with patch.object(host, "run") as run:
            self.assertIsNone(host.probe_media(f"http://127.0.0.1:{server.getsockname()[1]}/", ""))
            run.assert_not_called()
        self.assert_no_connections(server)

    @unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "requires installed yt-dlp")
    def test_real_ydl_transport_blocks_local_and_external_download(self):
        server = self.listener()
        with host.make_ydl([]) as ydl:
            with self.assertRaises(ValueError):
                ydl.urlopen(f"http://127.0.0.1:{server.getsockname()[1]}/")
            for protocol in ("m3u8", "rtmp"):
                with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                    ydl.dl(str(self.root / "unused"), {"url": "https://public.example/video", "protocol": protocol})
        self.assert_no_connections(server)

    @unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "requires installed yt-dlp")
    def test_ydl_response_adapter(self):
        from yt_dlp.networking import Request
        headers = Message()
        headers["Content-Type"] = "text/plain"
        response = addinfourl(io.BytesIO(b"public payload"), headers, "https://public.example/video", 200)
        with patch.object(urllib.request.OpenerDirector, "open", return_value=response) as opened, \
                host.make_ydl([]) as ydl:
            with ydl.urlopen(Request("https://public.example/video", headers={"Range": "bytes=0-"})) as result:
                self.assertEqual(result.status, 200)
                self.assertEqual(result.read(), b"public payload")
            request = opened.call_args.args[0]
            self.assertEqual(request.get_header("Range"), "bytes=0-")
            self.assertEqual(request.get_header("Accept-encoding"), "identity")

    def test_authenticated_cache_bypasses_old_entries_and_writes(self):
        path = host.info_cache_file("https://public.example/video")
        host.write_json_atomic(path, {"title": "old"})
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        fake.extract_info.return_value = {"title": "session"}
        fake.sanitize_info.side_effect = lambda info: info
        with patch.object(host, "AUTHENTICATED", True), patch.object(host, "make_ydl", return_value=fake):
            self.assertEqual(host.resolve_info("https://public.example/video"), {"title": "session"})
            self.assertIsNone(host.cached_cues(path))
            host.cache_json(self.root / "forbidden.json", ["private"])
        self.assertEqual(json.loads(path.read_text("utf-8")), {"title": "old"})
        self.assertFalse((self.root / "forbidden.json").exists())
        self.assertEqual(host.resolve_info("https://public.example/video"), {"title": "old"})

    def test_cookies_private_and_removed_on_success_and_error(self):
        for fail in (False, True):
            seen = []

            def handler(req, progress):
                seen.append(host.SESSION_FILE)
                self.assertTrue(host.AUTHENTICATED)
                self.assertTrue(host.SESSION_FILE.is_file())
                if os.name != "nt":
                    self.assertEqual(host.SESSION_FILE.stat().st_mode & 0o777, 0o600)
                if fail:
                    raise RuntimeError("handler failed")
                return {"ok": True}

            req = {"url": "https://public.example/video",
                   "cookies": "public.example\tFALSE\t/\tTRUE\t0\tsession\ttest"}
            with patch.object(host, "HANDLERS", {"resolve": handler}), \
                    patch.object(host, "contain_children"), patch.object(host, "sweep_stale_tmp"), \
                    patch.object(Path, "home", return_value=self.root), \
                    patch.object(host, "prune_cache", side_effect=PermissionError):
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "handler failed"):
                        host.process("resolve", req, Mock())
                else:
                    self.assertEqual(host.process("resolve", req, Mock()), {"ok": True})
            self.assertFalse(seen[0].exists())
            self.assertFalse(seen[0].parent.exists())
            self.assertIsNone(host.SESSION_FILE)
            self.assertFalse(host.AUTHENTICATED)

    def test_empty_cookies_still_disable_cache(self):
        def handler(req, progress):
            self.assertTrue(host.AUTHENTICATED)
            self.assertIsNone(host.SESSION_FILE)
            return {}
        with patch.object(host, "HANDLERS", {"resolve": handler}), patch.object(host, "contain_children"):
            host.process("resolve", {"cookies": ""}, Mock())

    def test_clear_history_revokes_old_file_and_metrics(self):
        folder = host.downloads_folder()
        folder.mkdir()
        saved = folder / "old.txt"
        saved.write_text("old", "utf-8")
        host.record_history(saved)
        self.assertEqual(host.allowed_output(str(saved)), saved)
        for path in (host.METRICS, host.METRICS.with_suffix(".1.jsonl")):
            path.write_text("private", "utf-8")
        self.assertEqual(host.HANDLERS["clear-history"]({}, Mock()), {"ok": True})
        with self.assertRaisesRegex(ValueError, "File not found"):
            host.allowed_output(str(saved))
        self.assertEqual(json.loads(host.HISTORY.read_text("utf-8")), [])
        self.assertFalse(host.METRICS.exists())
        self.assertFalse(host.METRICS.with_suffix(".1.jsonl").exists())

    def test_record_false_keeps_saved_file_without_history(self):
        source = self.root / "source.txt"
        source.write_text("payload", "utf-8")
        def handler(req, progress):
            return {"path": str(host.move_to_downloads(source))}
        with patch.object(host, "HANDLERS", {"download": handler}), patch.object(host, "contain_children"):
            result = host.process("download", {"record": False}, Mock())
        self.assertEqual(Path(result["path"]).read_text("utf-8"), "payload")
        self.assertFalse(host.HISTORY.exists())
        with self.assertRaises(ValueError):
            host.allowed_output(result["path"])
        self.assertTrue(host.RECORD_HISTORY)
        with self.assertRaises(ValueError):
            host.normalize({"action": "clear-history", "record": "false"})

    def test_metrics_do_not_reappear_after_clear_or_without_record(self):
        with patch.object(host, "load_config", side_effect=AssertionError("must return before config")):
            host.record_metrics("clear-history", {"metrics": True}, Mock(), "done", {})
            host.record_metrics("download", {"metrics": True, "record": False}, Mock(), "done", {})
        self.assertFalse(host.METRICS.exists())

    def test_concurrent_destinations_do_not_overwrite(self):
        sources = [self.root / f"source-{i}.txt" for i in range(8)]
        for i, source in enumerate(sources):
            source.write_text(str(i), "utf-8")
        with patch.object(host, "record_history"), ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(pool.map(lambda source: host.move_to_downloads(source, "same.txt"), sources))
        self.assertEqual(len(set(outputs)), len(sources))
        self.assertEqual({p.read_text("utf-8") for p in outputs}, {str(i) for i in range(8)})

    def test_move_links_without_copy_and_falls_back_across_volumes(self):
        with patch.object(host, "record_history"):
            first = self.root / "a.txt"
            first.write_text("one", "utf-8")
            with patch.object(host.shutil, "copyfileobj", side_effect=AssertionError("copied")):
                moved = host.move_to_downloads(first, "x.txt")
            self.assertEqual(moved.read_text("utf-8"), "one")
            self.assertFalse(first.exists())
            second = self.root / "b.txt"
            second.write_text("two", "utf-8")
            again = host.move_to_downloads(second, "x.txt")
            self.assertNotEqual(again, moved)
            self.assertEqual(moved.read_text("utf-8"), "one")
            third = self.root / "c.txt"
            third.write_text("three", "utf-8")
            with patch.object(host.os, "link", side_effect=OSError("cross-device")):
                copied = host.move_to_downloads(third, "y.txt")
            self.assertEqual(copied.read_text("utf-8"), "three")
            self.assertFalse(third.exists())

    def test_names_are_portable_and_byte_bounded(self):
        self.assertLessEqual(len(host.safe_name("🎬" * 150).encode("utf-8")), 150)
        for name in ("CON", "con.txt", "CONIN$", "CONOUT$.mp4", "NUL. ", "COM1.mp4", "LPT9.txt", "COM¹.txt", "AUX .mp4"):
            with self.subTest(name=name):
                self.assertTrue(host.safe_name(name).startswith("_"))
        source = self.root / "source.txt"
        source.write_text("payload", "utf-8")
        destination = host.move_to_downloads(source, "🎬" * 150 + ".txt")
        self.assertLessEqual(len(destination.name.encode("utf-8")), 240)
        self.assertEqual(destination.suffix, ".txt")

    def test_invalid_translation_never_succeeds(self):
        for raw in ("not json", '[]', '[null]', '[4]', '[{}]', '[""]', '[" "]'):
            with self.subTest(raw=raw), patch.object(ai, "complete", return_value=raw):
                with self.assertRaises(ai.AIError):
                    ai.translate_lines({}, ["hello"], "Spanish", host.T)
        with patch.object(ai, "complete", return_value='[" hola "]'):
            self.assertEqual(ai.translate_lines({}, ["hello"], "Spanish", host.T), ["hola"])
        progress = Mock()
        with patch.object(ai, "BATCH", 1), patch.object(ai, "complete", side_effect=['["hola"]', '[]']):
            with self.assertRaises(ai.AIError):
                ai.translate_lines({}, ["hello", "world"], "Spanish", host.T, progress)
        progress.assert_called_once_with(50.0)

    def test_release_version_and_launcher(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text("utf-8"))
        self.assertEqual(host.BACKEND_VERSION, manifest["version"])
        self.assertIn('python.exe" -I -u ', (ROOT / "backend" / "host.bat").read_text("utf-8"))

    def test_download_guard_allows_native_transports_and_blocks_ffmpeg(self):
        from yt_dlp.downloader.external import FFmpegFD
        self.addCleanup(setattr, FFmpegFD, "real_download", FFmpegFD.real_download)
        ydl = Mock()
        ydl.params = {}
        ydl.dl = Mock(return_value="ok")
        host.guard_redirects(ydl)
        for name, allowed in {"HttpFD": True, "DashSegmentsFD": True, "HlsFD": True, "FFmpegFD": False, "Aria2cFD": False}.items():
            with self.subTest(downloader=name):
                fd = type(name, (), {})
                with patch("yt_dlp.downloader.get_suitable_downloader", return_value=fd):
                    if allowed:
                        self.assertEqual(ydl.dl("f", {}), "ok")
                    else:
                        with self.assertRaises(ValueError):
                            ydl.dl("f", {})
        with self.assertRaises(ValueError):
            FFmpegFD.real_download(Mock(), "f", {})


if __name__ == "__main__":
    unittest.main()
