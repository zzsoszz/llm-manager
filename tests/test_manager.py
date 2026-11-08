import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from llm_manager import cli, config, downloads, runtime, telemetry


class HTTPFixture:
    def __enter__(self):
        fixture = self
        self.paths = []
        self.payloads = []
        self.result = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def reply(self, data, code=200):
                self.send_response(code)
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            def do_GET(self):
                fixture.paths.append(self.path)
                if self.path == '/v1/models':
                    self.reply({'data': [{'id': 'fixture-model'}]})
                else:
                    self.reply({}, 404)
            def do_POST(self):
                fixture.paths.append(self.path)
                fixture.payloads.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.reply({'result': fixture.result})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        return self
    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'config.json'

    def write(self, data):
        self.path.write_text(json.dumps(data))
        return config.load(self.path)

    def test_missing_config_has_init_hint(self):
        with self.assertRaisesRegex(config.ConfigError, 'llm init'):
            config.load(self.path)

    def test_duplicate_identity_rejected(self):
        with self.assertRaisesRegex(config.ConfigError, '重复模型'):
            self.write({'version': 1, 'models': [{'name': 'qwen', 'engine': 'sglang'}]*2})

    def test_invalid_list_type_rejected(self):
        for value in (None, {}, 'bad'):
            with self.subTest(value=value), self.assertRaises(config.ConfigError):
                self.write({'version': 1, 'models': value})

    def test_missing_download_file_rejected(self):
        with self.assertRaises(config.ConfigError):
            self.write({'version': 1, 'downloads': [{'name': 'weights'}]})

    def test_nested_variables_and_missing_variable(self):
        d = self.write({'version': 1, 'variables': {'root': '${config_dir}/models'}})
        values = config.variables(d, {'name': 'qwen', 'variables': {'weights': '${root}/${model}'}})
        self.assertEqual(config.expand('${weights}', values), str(self.path.parent/'models/qwen'))
        with self.assertRaises(config.ConfigError):
            config.expand('${unknown}', values)

    def test_cyclic_variables_rejected(self):
        values = config.variables({'_path': str(self.path), 'variables': {'a': '${b}', 'b': '${a}'}}, {})
        with self.assertRaises(config.ConfigError):
            config.expand('${a}', values)

    def test_secret_redaction_preserves_env_references(self):
        v = config.redacted({'api_key': 'secret', 'api_key_env': 'MODEL_API_KEY', 'env': {'MY_TOKEN': 'secret'}, 'argv': ['x', '--api-key', 'secret', '--password=secret']})
        self.assertEqual(v['api_key_env'], 'MODEL_API_KEY')
        self.assertNotIn('secret', json.dumps(v))

    def test_shell_strings_rejected_and_arguments_literal(self):
        d = {'_path': str(self.path)}
        with self.assertRaises(config.ConfigError):
            runtime.command(d, {'commands': {'start': 'echo bad'}}, 'start')
        argv, _, _ = runtime.command(d, {'commands': {'start': ['echo', '$(touch /tmp/not-executed)']}}, 'start')
        self.assertEqual(argv[1], '$(touch /tmp/not-executed)')

    def test_command_exit_status_and_cwd(self):
        d = {'_path': str(self.path)}
        entry = {'commands': {'start': {'argv': [sys.executable, '-c', 'import sys; sys.exit(7)'], 'cwd': '${config_dir}'}}}
        self.assertEqual(runtime.run(d, entry, 'start', []), 7)

    def test_log_dry_plan_does_not_require_file(self):
        d = {'_path': str(self.path)}
        e = {'logs': {'type': 'file', 'path': '${config_dir}/not-created.log'}}
        cmd = runtime.log_command(d, e, 15, True)
        self.assertIn('-F', cmd)
        self.assertEqual(cmd[-1], str(self.path.parent/'not-created.log'))


class TelemetryTests(unittest.TestCase):
    def line(self, stamp='2026-11-08 12:00:00', speed='12.65'):
        return f'[{stamp}] Decode batch, #running-req: 1, gen throughput (token/s): {speed}, #queue-req: 2'

    def test_batch_parse_and_restart_boundary(self):
        text = self.line(speed='99')+'\nserver_args={}\n'+self.line()
        points = telemetry.parse_sglang(text)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]['speed'], 12.65)
        self.assertEqual(points[0]['queued'], 2)

    def test_malformed_and_missing_speed_ignored(self):
        self.assertEqual(telemetry.parse_sglang(self.line(stamp='2026-99-99 12:00:00')), [])
        self.assertEqual(telemetry.parse_sglang('[2026-11-08 12:00:00] Decode batch, #running-req: 1'), [])

    def test_fresh_stale_offline_and_future(self):
        points = telemetry.parse_sglang(self.line())
        now = points[0]['timestamp']
        for online, delta, expected in [(True, 1, 12.65), (True, 30, None), (False, 1, None), (True, -60, None)]:
            result = telemetry.summarize(points, online, now=now+delta)
            self.assertEqual(result['decode']['tokens_per_second'], expected)
            if expected is None:
                self.assertIsNone(result['running_sample'])

    def test_no_data_does_not_invent_zero_speed(self):
        result = telemetry.summarize([], True)
        self.assertIsNone(result['decode']['tokens_per_second'])
        self.assertIsNone(result['running_sample'])

    def test_probe_only_reads_model_list(self):
        with HTTPFixture() as server:
            data = {'_path': '/tmp/config.json'}
            target = {'name': 'qwen', 'engine': 'sglang', 'api': {'base_url': server.url+'/v1'}, 'monitor': {'type': 'sglang-log', 'path': '/nonexistent/log'}}
            result = telemetry.snapshot(data, target, no_gpu=True)
            self.assertTrue(result['online'])
            self.assertEqual(server.paths, ['/v1/models'])

    def test_amd_metrics_from_fixture(self):
        with tempfile.TemporaryDirectory() as root:
            device = Path(root)/'card1/device'
            hw = device/'hwmon/hwmon0'
            hw.mkdir(parents=True)
            for key, val in {'vendor': '0x1002', 'gpu_busy_percent': '15', 'mem_info_vram_used': str(2**30), 'mem_info_vram_total': str(24*2**30)}.items():
                (device/key).write_text(val)
            (hw/'power1_average').write_text('45000000')
            rows = telemetry.gpu_stats(Path(root), nvidia=False)
            self.assertEqual(rows[0]['power_w'], 45)
            self.assertEqual(rows[0]['vram_used_mib'], 1024)


class DownloadTests(unittest.TestCase):
    def test_rpc_selects_matching_file_and_eta(self):
        with HTTPFixture() as server:
            server.result = [
                {'files': [{'path': '/tmp/unrelated'}], 'completedLength': '999', 'totalLength': '1000', 'downloadSpeed': '50'},
                {'files': [{'path': '/tmp/weights'}], 'completedLength': '200', 'totalLength': '1000', 'downloadSpeed': '100'},
            ]
            row = downloads.snapshot({'_path': '/tmp/config.json'}, {'name': 'qwen', 'file': '/tmp/weights', 'rpc_url': server.url+'/jsonrpc'})
            self.assertEqual(row['completed_bytes'], 200)
            self.assertEqual(row['eta_seconds'], 8)
            self.assertEqual(server.payloads[0]['method'], 'aria2.tellActive')

    def test_disk_fallback_is_not_verification(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'weights';p.write_bytes(b'test')
            item = {'name': 'qwen', 'file': str(p), 'total_bytes': 4}
            row = downloads.snapshot({'_path': root+'/config.json'}, item)
            self.assertFalse(row['verified'])
            self.assertEqual(row['source'], '磁盘估算')
            self.assertIsNone(row['eta_seconds'])

    def test_explicit_verification_marker(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root)/'verified.json';marker.write_text('{}')
            item = {'name': 'qwen', 'file': root+'/weights', 'total_bytes': 10, 'verified_marker': str(marker)}
            row = downloads.snapshot({'_path': root+'/config.json'}, item)
            self.assertTrue(row['verified'])
            self.assertEqual(row['completed_bytes'], 10)


class CLITests(unittest.TestCase):
    def test_legacy_order_and_follow(self):
        self.assertEqual(cli.normalize(['qwen', 'sglang', 'start']), ['start', 'qwen', 'sglang'])
        self.assertEqual(cli.normalize(['qwen', 'sglang', 'log']), ['log', 'qwen', 'sglang', '-f'])
        self.assertEqual(cli.normalize(['--dry-run', 'qwen', 'sglang', 'start']), ['--dry-run', 'start', 'qwen', 'sglang'])

    def test_init_never_overwrites_existing_config(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            path = Path(root)/'config.json'
            self.assertEqual(cli.main(['-c', str(path), 'init']), 0)
            first = path.read_bytes()
            self.assertEqual(cli.main(['-c', str(path), 'init']), 2)
            self.assertEqual(path.read_bytes(), first)
            config.load(path)

    def test_watch_rejects_nonfinite_interval(self):
        for value in (0, float('nan'), float('inf')):
            with self.assertRaises(config.ConfigError):
                cli.watch(lambda: None, str, interval=value, once=True, as_json=True)

    def test_configuration_is_required_before_executing(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['-c', root+'/missing', 'start', 'qwen', 'sglang']), 2)


if __name__ == '__main__':
    unittest.main()
