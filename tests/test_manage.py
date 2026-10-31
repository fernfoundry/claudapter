"""Offline lifecycle tests. Never use the repository's private directory or real PIDs."""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import signal
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'manage.py'


def load_module():
    spec = importlib.util.spec_from_file_location('gateway_manage_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportTests(unittest.TestCase):
    def test_import_and_unknown_mode_have_no_side_effects(self):
        with mock.patch('pathlib.Path.mkdir', side_effect=AssertionError('mkdir')), \
                mock.patch('os.chmod', side_effect=AssertionError('chmod')), \
                mock.patch('shutil.copyfile', side_effect=AssertionError('copy')), \
                mock.patch('shutil.which', side_effect=AssertionError('runtime lookup')), \
                mock.patch('subprocess.check_output', side_effect=AssertionError('runtime probe')):
            module = load_module()
            self.assertIsNone(module.NODE)
            with self.assertRaisesRegex(RuntimeError, '未知 mode'):
                module.main(['invalid'])


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.m = load_module()
        self.m.ROOT = pathlib.Path(self.temp.name)
        self.m.PRIVATE = self.m.ROOT / 'private'
        self.m.PRIVATE.mkdir()
        (self.m.ROOT / 'src').mkdir()
        (self.m.ROOT / 'src/server.js').write_text('// test source')
        self.m.NODE = '/fake/node'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.cfg = {'model': 'gpt-example', 'reasoning': 'medium', 'port': self.port, 'https_proxy': ''}
        self.cred = {'api_key': 'sk-fake-offline-only'}
        self.config_path = self.m.ROOT / 'config.json'
        self.config_path.write_text(json.dumps(self.cfg))
        self.state_path = self.m.PRIVATE / 'state.json'
        self.proc = mock.Mock(pid=424242)
        self.proc.poll.return_value = None
        self.popen = mock.patch.object(self.m.subprocess, 'Popen', return_value=self.proc).start()
        self.kill = mock.patch.object(self.m.os, 'kill').start()
        mock.patch.object(self.m, 'is_zombie', return_value=False).start()
        self.request = mock.patch.object(self.m, 'request').start()
        mock.patch.object(self.m, 'credentials', return_value=self.cred).start()
        mock.patch.object(self.m.time, 'sleep').start()
        self.addCleanup(mock.patch.stopall)
        self.m.START_ATTEMPTS = self.m.STOP_ATTEMPTS = 3
        self.request.side_effect = self.valid_spawn_health

    def valid_spawn_health(self, url, token):
        env = self.popen.call_args.kwargs['env']
        self.assertEqual(token, env['GPT_PROXY_TOKEN'])
        self.assertEqual(url, f'http://127.0.0.1:{self.port}/health')
        return {'ok': True, 'service': self.m.SERVICE, 'pid': self.proc.pid,
                'instance_id': env['GPT_PROXY_INSTANCE']}

    def stored_state(self):
        state = {'pid': self.proc.pid, 'url': f'http://127.0.0.1:{self.port}',
                 'token': 'local-token', 'instance_id': 'old-instance',
                 'config_fingerprint': self.m.config_fingerprint(self.cred, self.cfg, 'local-token')}
        self.state_path.write_text(json.dumps(state))
        self.request.side_effect = None
        self.request.return_value = {'ok': True, 'service': self.m.SERVICE,
                                     'pid': state['pid'], 'instance_id': state['instance_id']}
        return state

    def test_start_saves_authenticated_live_instance(self):
        state, _, _ = self.m.start()
        self.assertEqual(json.loads(self.state_path.read_text()), state)
        self.assertEqual(state['pid'], self.proc.pid)
        self.assertEqual(len(state['config_fingerprint']), 64)
        self.assertNotIn(self.cred['api_key'], self.state_path.read_text())
        self.assertEqual(self.proc.poll.call_count, 2)
        self.proc.terminate.assert_not_called()
        self.kill.assert_not_called()
        self.assertTrue(self.popen.call_args.kwargs['start_new_session'])

    def test_port_conflict_never_probes_or_spawns(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', self.port))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, '已占用'):
                self.m.start()
        self.request.assert_not_called()
        self.popen.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_closed_listener_time_wait_allows_start(self):
        # Close the accepted/server side first, leaving TIME_WAIT on its port.
        # These are fresh loopback sockets, never the user's configured gateway.
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            self.port = listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1', self.port), timeout=2) as client:
                accepted, _ = listener.accept()
                accepted.close()
                self.assertEqual(client.recv(1), b'')
        self.cfg['port'] = self.port
        self.config_path.write_text(json.dumps(self.cfg))
        state, _, _ = self.m.start()
        self.assertEqual(state['url'], f'http://127.0.0.1:{self.port}')
        self.popen.assert_called_once()

    def test_reuses_only_verified_unchanged_state(self):
        state = self.stored_state()
        self.assertEqual(self.m.start()[0], state)
        self.request.assert_called_once_with(state['url'] + '/health', state['token'])
        self.kill.assert_called_once_with(state['pid'], 0)
        self.popen.assert_not_called()

    def test_config_and_key_changes_require_restart(self):
        for field, value in [('model', 'gpt-other'), ('reasoning', 'high'),
                             ('port', self.port + 1), ('https_proxy', 'http://proxy.invalid:1234')]:
            with self.subTest(field=field):
                self.config_path.write_text(json.dumps(self.cfg))
                state = self.stored_state()
                self.config_path.write_text(json.dumps(dict(self.cfg, **{field: value})))
                with self.assertRaisesRegex(RuntimeError, 'restart'):
                    self.m.start()
                self.assertEqual(json.loads(self.state_path.read_text()), state)
        self.config_path.write_text(json.dumps(self.cfg))
        self.stored_state()
        self.cred['api_key'] = 'sk-new-offline'
        with self.assertRaisesRegex(RuntimeError, 'restart'):
            self.m.start()
        self.popen.assert_not_called()
        self.assertTrue(all(call.args[1] == 0 for call in self.kill.call_args_list))

    def test_fingerprint_tracks_source_node_environment_and_hides_key(self):
        baseline = self.m.config_fingerprint(self.cred, self.cfg, 'token')
        for name in self.m.PROXY_ENV + ('NO_PROXY', 'no_proxy', 'NODE_EXTRA_CA_CERTS', 'NODE_OPTIONS'):
            with self.subTest(env=name), mock.patch.dict(os.environ, {name: 'different-value'}):
                self.assertNotEqual(baseline, self.m.config_fingerprint(self.cred, self.cfg, 'token'))
        (self.m.ROOT / 'src/server.js').write_text('// changed source')
        self.assertNotEqual(baseline, self.m.config_fingerprint(self.cred, self.cfg, 'token'))
        (self.m.ROOT / 'src/server.js').write_text('// test source')
        self.m.NODE = '/other/node'
        self.assertNotEqual(baseline, self.m.config_fingerprint(self.cred, self.cfg, 'token'))
        self.m.NODE = '/fake/node'
        self.assertNotEqual(baseline, self.m.config_fingerprint(self.cred, self.cfg, 'other-token'))

    def test_direct_mode_clears_inherited_proxies(self):
        with mock.patch.dict(os.environ, {key: 'inherited' for key in self.m.PROXY_ENV}):
            self.m.start()
        env = self.popen.call_args.kwargs['env']
        for key in self.m.PROXY_ENV:
            self.assertNotIn(key, env)
        self.assertEqual(env['OPENAI_API_KEY'], self.cred['api_key'])

    def test_explicit_proxy_overrides_all_inherited_proxies(self):
        cfg = dict(self.cfg, https_proxy='http://proxy.invalid:1234')
        with mock.patch.dict(os.environ, {key: 'inherited' for key in self.m.PROXY_ENV}):
            env = self.m.child_environment(self.cred, cfg, 'token', 'instance')
        self.assertEqual(env['HTTPS_PROXY'], cfg['https_proxy'])
        self.assertEqual(env['NODE_USE_ENV_PROXY'], '1')
        self.assertEqual(env['NO_PROXY'], '127.0.0.1,localhost')
        for key in set(self.m.PROXY_ENV) - {'HTTPS_PROXY', 'NODE_USE_ENV_PROXY'}:
            self.assertNotIn(key, env)

    def test_mismatched_identity_start_and_stop_never_kill_or_overwrite(self):
        for field, value in [('service', 'other'), ('pid', 12345), ('pid', True),
                             ('instance_id', 'other'), ('ok', False)]:
            for action in (self.m.start, self.m.stop):
                with self.subTest(field=field, action=action.__name__):
                    state = self.stored_state()
                    self.request.return_value[field] = value
                    self.kill.reset_mock()
                    with self.assertRaisesRegex(RuntimeError, '身份不匹配'):
                        action()
                    self.kill.assert_not_called()
                    self.assertEqual(json.loads(self.state_path.read_text()), state)
        self.popen.assert_not_called()

    def test_old_state_and_remote_urls_rejected_before_requests(self):
        for url in ('https://127.0.0.1:1234', 'http://localhost:1234',
                    'http://127.0.0.1:1234@evil.test', 'http://127.0.0.1:1234/path',
                    'http://127.0.0.1:0', 'http://evil.test:1234'):
            state = self.stored_state()
            state['url'] = url
            self.state_path.write_text(json.dumps(state))
            self.request.reset_mock()
            for action in (self.m.start, self.m.stop):
                with self.assertRaises(RuntimeError): action()
            self.request.assert_not_called()
        self.state_path.write_text(json.dumps({'pid': 424242, 'url': f'http://127.0.0.1:{self.port}', 'token': 'old'}))
        for action in (self.m.start, self.m.stop):
            with self.assertRaisesRegex(RuntimeError, '旧版 state'): action()
        self.kill.assert_not_called()
        self.popen.assert_not_called()

    def test_unreachable_existing_state_is_not_replaced(self):
        state = self.stored_state()
        self.request.side_effect = TimeoutError('offline')
        for action in (self.m.start, self.m.stop):
            with self.assertRaises(TimeoutError): action()
        self.assertEqual(json.loads(self.state_path.read_text()), state)
        self.popen.assert_not_called()
        self.kill.assert_not_called()

    def test_start_timeout_and_wrong_identity_cleanup_only_owned_child(self):
        for response in (TimeoutError('not ready'), {'ok': True, 'service': 'other', 'pid': 999, 'instance_id': 'wrong'}):
            with self.subTest(response=response):
                self.request.side_effect = response if isinstance(response, Exception) else None
                self.request.return_value = response
                with self.assertRaisesRegex(RuntimeError, '启动超时'):
                    self.m.start()
                self.assertFalse(self.state_path.exists())
        self.assertEqual(self.proc.terminate.call_count, 2)
        self.assertEqual(self.proc.wait.call_count, 2)
        self.kill.assert_not_called()

    def test_dead_child_is_never_saved(self):
        self.proc.poll.side_effect = [None, 1, 1]
        with self.assertRaisesRegex(RuntimeError, '立即退出'):
            self.m.start()
        self.assertFalse(self.state_path.exists())
        self.proc.terminate.assert_not_called()

    def test_start_timeout_reaps_child_that_ignores_terminate(self):
        self.request.side_effect = TimeoutError()
        self.proc.wait.side_effect = [subprocess.TimeoutExpired('fake-node', 2), 0]
        with self.assertRaisesRegex(RuntimeError, '启动超时'):
            self.m.start()
        self.proc.kill.assert_called_once()
        self.assertFalse(self.state_path.exists())

    def test_stop_waits_for_confirmed_exit(self):
        state = self.stored_state()
        self.kill.side_effect = [None, None, None, ProcessLookupError()]
        self.m.stop()
        self.assertEqual(self.kill.call_args_list, [mock.call(state['pid'], 0),
                         mock.call(state['pid'], signal.SIGTERM), mock.call(state['pid'], 0),
                         mock.call(state['pid'], 0)])
        self.assertFalse(self.state_path.exists())

    def test_stop_timeout_or_permission_error_retains_state(self):
        for error in (None, PermissionError('denied')):
            state = self.stored_state()
            self.kill.reset_mock()
            self.kill.side_effect = error
            with self.assertRaises((RuntimeError, PermissionError)):
                self.m.stop()
            self.assertEqual(json.loads(self.state_path.read_text()), state)
            self.assertNotIn(mock.call(state['pid'], signal.SIGKILL), self.kill.call_args_list)

    def test_missing_pid_rejects_start_and_stop_and_retains_state(self):
        state = self.stored_state()
        self.kill.side_effect = ProcessLookupError()
        for action in (self.m.start, self.m.stop):
            with self.assertRaisesRegex(RuntimeError, 'PID 不存在'): action()
        self.assertEqual(json.loads(self.state_path.read_text()), state)
        self.popen.assert_not_called()

    def test_restart_explicit_order_and_no_start_after_failed_stop(self):
        calls = []
        with mock.patch.object(self.m, 'stop', side_effect=lambda: calls.append('stop')), \
                mock.patch.object(self.m, 'start', side_effect=lambda: calls.append('start')):
            self.m.restart()
        self.assertEqual(calls, ['stop', 'start'])
        with mock.patch.object(self.m, 'stop', side_effect=RuntimeError('unsafe')), \
                mock.patch.object(self.m, 'start') as start:
            with self.assertRaises(RuntimeError): self.m.restart()
            start.assert_not_called()

    def test_health_transport_is_local_short_timeout_and_authenticated(self):
        # Unpatch request only in this test; HTTPConnection is still mocked, no real network.
        real_request = load_module().request
        with mock.patch('http.client.HTTPConnection') as connection:
            reply = connection.return_value.getresponse.return_value
            reply.status = 200
            reply.read.return_value = b'{"ok":true}'
            self.assertEqual(real_request(f'http://127.0.0.1:{self.port}/health', 'secret'), {'ok': True})
            connection.assert_called_once_with('127.0.0.1', self.port, timeout=self.m.HEALTH_TIMEOUT)
            connection.return_value.request.assert_called_once_with('GET', '/health', headers={'Authorization': 'Bearer secret'})
            connection.return_value.close.assert_called_once()


class UsabilityTests(unittest.TestCase):
    """Every path and identity is synthetic; no runtime, API or live gateway access."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.m = load_module()
        self.m.ROOT = pathlib.Path(self.temp.name)
        self.m.PRIVATE = self.m.ROOT / 'private'
        self.m.NODE = '/fake/node'
        self.cfg = {'model': 'gpt-offline', 'reasoning': 'medium', 'port': 18789, 'https_proxy': ''}
        self.state = {'url': 'http://127.0.0.1:18789', 'pid': 424242,
                      'token': 'SECRET-TOKEN', 'instance_id': 'SECRET-INSTANCE',
                      'config_fingerprint': 'SECRET-FINGERPRINT', 'model': 'SECRET-MODEL',
                      'api_key': 'sk-SECRET-KEY'}

    def write_config(self, value=None):
        (self.m.ROOT / 'config.json').write_text(json.dumps(self.cfg if value is None else value))

    def write_state(self, value=None):
        self.m.PRIVATE.mkdir(exist_ok=True)
        (self.m.PRIVATE / 'state.json').write_text(json.dumps(self.state if value is None else value))

    def write_key(self, value):
        self.m.PRIVATE.mkdir(exist_ok=True)
        (self.m.PRIVATE / 'credentials.json').write_text(json.dumps({'api_key': value}))

    @contextlib.contextmanager
    def read_only(self):
        with mock.patch.object(self.m, 'initialize', side_effect=AssertionError('initialize')), \
                mock.patch.object(self.m, 'find_node', side_effect=AssertionError('runtime')), \
                mock.patch.object(self.m.shutil, 'which', side_effect=AssertionError('lookup')), \
                mock.patch.object(self.m.subprocess, 'check_output', side_effect=AssertionError('probe')), \
                mock.patch.object(self.m, 'credentials', side_effect=AssertionError('credentials')), \
                mock.patch.object(self.m, 'load_config', side_effect=AssertionError('config')), \
                mock.patch.object(self.m, 'save', side_effect=AssertionError('save')), \
                mock.patch.object(pathlib.Path, 'mkdir', side_effect=AssertionError('mkdir')), \
                mock.patch.object(pathlib.Path, 'write_text', side_effect=AssertionError('write')), \
                mock.patch.object(self.m.os, 'open', side_effect=AssertionError('open/write')), \
                mock.patch.object(self.m.os, 'chmod', side_effect=AssertionError('chmod')), \
                mock.patch.object(self.m.shutil, 'copyfile', side_effect=AssertionError('copy')):
            yield

    def test_help_variants_have_no_effects_or_runtime_probes(self):
        variants = [['help'], ['-h'], ['--help'], ['help', 'status']]
        variants += [[mode, flag] for mode in self.m.MODES for flag in ('-h', '--help')]
        with self.read_only(), mock.patch.object(self.m, 'request') as request, \
                mock.patch.object(self.m.os, 'kill') as kill:
            for args in variants:
                with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.m.main(args)
                    self.assertIn('用法', output.getvalue())
                    self.assertIn('launch -- --help', output.getvalue())
            request.assert_not_called()
            kill.assert_not_called()
        self.assertFalse(self.m.PRIVATE.exists())

    def test_non_launch_extra_arguments_rejected_before_effects(self):
        with self.read_only(), mock.patch.object(self.m, 'request') as request:
            for mode in self.m.MODES:
                if mode in ('launch', 'connect'):
                    continue
                with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, '额外参数'):
                    self.m.main([mode, 'SECRET-UNEXPECTED'])
            request.assert_not_called()
        self.assertFalse(self.m.PRIVATE.exists())

    def test_status_absent_is_not_managed_and_does_not_claim_free_port(self):
        with self.read_only(), mock.patch.object(self.m, 'request') as request, \
                mock.patch.object(self.m.os, 'kill') as kill, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.m.main(['status'])
        self.assertIn('未受管理', output.getvalue())
        self.assertIn('未检查端口是否空闲', output.getvalue())
        request.assert_not_called()
        kill.assert_not_called()
        self.assertFalse(self.m.PRIVATE.exists())

    def test_status_healthy_uses_authenticated_local_health_and_pid_only(self):
        self.write_state()
        before = (self.m.PRIVATE / 'state.json').read_bytes()
        reply = {'ok': True, 'service': self.m.SERVICE, 'pid': self.state['pid'],
                 'instance_id': self.state['instance_id'], 'model': 'SECRET-REPLY-MODEL'}
        with self.read_only(), mock.patch.object(self.m, 'request', return_value=reply) as request, \
                mock.patch.object(self.m.os, 'kill') as kill, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.m.main(['status'])
        self.assertEqual(output.getvalue(), '网关运行正常：http://127.0.0.1:18789 PID 424242\n')
        request.assert_called_once_with(self.state['url'] + '/health', self.state['token'])
        kill.assert_called_once_with(self.state['pid'], 0)
        self.assertEqual((self.m.PRIVATE / 'state.json').read_bytes(), before)

    def test_status_failures_are_sanitized_and_never_write(self):
        cases = ('malformed', 'array', 'remote', 'bad_pid', 'mismatch', 'unreachable', 'missing_pid', 'permission')
        for case in cases:
            with self.subTest(case=case):
                self.write_state()
                path = self.m.PRIVATE / 'state.json'
                if case == 'malformed':
                    path.write_text('{SECRET-MALFORMED')
                elif case == 'array':
                    path.write_text('["SECRET-ARRAY"]')
                elif case == 'remote':
                    self.write_state(dict(self.state, url='http://SECRET-REMOTE:1234'))
                elif case == 'bad_pid':
                    self.write_state(dict(self.state, pid=True))
                before = path.read_bytes()
                reply = {'ok': True, 'service': self.m.SERVICE, 'pid': self.state['pid'],
                         'instance_id': self.state['instance_id']}
                if case == 'mismatch':
                    reply['instance_id'] = 'SECRET-MISMATCH'
                with self.read_only(), mock.patch.object(self.m, 'request', return_value=reply) as request, \
                        mock.patch.object(self.m.os, 'kill') as kill, \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    if case == 'unreachable':
                        request.side_effect = TimeoutError('SECRET-EXCEPTION')
                    if case in ('missing_pid', 'permission'):
                        kill.side_effect = (ProcessLookupError('SECRET-PID') if case == 'missing_pid'
                                            else PermissionError('SECRET-PERMISSION'))
                    with self.assertRaisesRegex(RuntimeError, '无法确认网关状态') as error:
                        self.m.main(['status'])
                    self.assertNotIn('SECRET', str(error.exception) + output.getvalue())
                    self.assertIsNone(error.exception.__cause__)
                    if case in ('malformed', 'array', 'remote', 'bad_pid'):
                        request.assert_not_called()
                    self.assertTrue(all(call.args[1] == 0 for call in kill.call_args_list))
                self.assertEqual(path.read_bytes(), before)

    def test_stop_does_not_initialize_or_require_config_credentials_or_node(self):
        self.m.NODE = None
        with self.read_only(), mock.patch.object(self.m, 'request') as request, \
                contextlib.redirect_stdout(io.StringIO()):
            self.m.main(['stop'])
        request.assert_not_called()
        self.assertFalse(self.m.PRIVATE.exists())

    def test_invalid_configs_fail_before_spawn_prompt_or_api(self):
        invalid = [None, '{SECRET-BROKEN', '[]']
        invalid += [json.dumps(dict(self.cfg, **change)) for change in (
            {'model': 'YOUR_RESPONSES_MODEL_ID'}, {'model': None}, {'model': ''}, {'model': 'two words'},
            {'reasoning': []}, {'reasoning': ''}, {'port': True}, {'port': '18789'},
            {'port': 0}, {'port': 65536}, {'port': 1.5}, {'https_proxy': None},
            {'https_proxy': 'SECRET-PROXY'}, {'https_proxy': 'socks5://proxy.invalid:1234'},
            {'https_proxy': 'http://proxy.invalid:0'}, {'https_proxy': 'http://proxy.invalid:65536'},
            {'https_proxy': 'http://proxy.invalid'}, {'https_proxy': 'http://proxy.invalid:1234/path'},
            {'https_proxy': 'http://proxy.invalid:1234?q=SECRET'},
        )]
        invalid += [json.dumps({}), json.dumps({'model': 'gpt-offline'})]
        for raw in invalid:
            with self.subTest(raw=raw):
                path = self.m.ROOT / 'config.json'
                if path.exists():
                    path.unlink()
                if raw is not None:
                    path.write_text(raw)
                with mock.patch.object(self.m, 'initialize'), \
                        mock.patch.object(self.m, 'credentials') as credentials, \
                        mock.patch.object(self.m.subprocess, 'Popen') as spawn, \
                        mock.patch.object(self.m.getpass, 'getpass') as prompt, \
                        mock.patch.object(self.m.urllib.request, 'build_opener') as opener:
                    for mode in ('start', 'setup'):
                        with self.assertRaises(RuntimeError) as error:
                            self.m.main([mode])
                        self.assertNotIn('SECRET', str(error.exception))
                    with self.assertRaises(RuntimeError):
                        self.m.request('https://api.openai.com/v1/models', 'sk-offline')
                    credentials.assert_not_called()
                    spawn.assert_not_called()
                    prompt.assert_not_called()
                    opener.assert_not_called()

    def test_credentials_and_setup_reject_invalid_keys_without_api(self):
        self.write_config()
        for key in (None, 123, '', 'sk-', 'not-a-key', ' sk-offline', 'sk-offline ', 'sk-\nkey', 'sk-\x00key'):
            with self.subTest(key=key):
                self.write_key(key)
                with self.assertRaisesRegex(RuntimeError, 'api_key 无效'):
                    self.m.credentials()
                (self.m.PRIVATE / 'credentials.json').unlink()
                with mock.patch.object(self.m, 'initialize'), \
                        mock.patch.object(self.m.getpass, 'getpass', return_value=key), \
                        mock.patch.object(self.m, 'request') as request, \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, 'Key 格式'):
                        self.m.main(['setup'])
                    request.assert_not_called()
                self.assertFalse((self.m.PRIVATE / 'credentials.json').exists())
        self.write_key('sk-offline-valid')
        self.assertEqual(self.m.credentials()['api_key'], 'sk-offline-valid')

    def test_state_token_and_credentials_read_object_errors_are_friendly(self):
        self.write_config()
        self.write_key('sk-offline')
        for name, action in (('credentials.json', self.m.credentials),
                             ('state.json', self.m.stop), ('gateway-token.json', self.m.start)):
            path = self.m.PRIVATE / name
            for raw in ('{SECRET-MALFORMED', '["SECRET-ARRAY"]'):
                with self.subTest(name=name, raw=raw):
                    path.write_text(raw)
                    with mock.patch.object(self.m.subprocess, 'Popen') as spawn, \
                            mock.patch.object(self.m.socket, 'socket'):
                        with self.assertRaises(RuntimeError) as error:
                            action()
                        self.assertNotIn('SECRET', str(error.exception))
                        spawn.assert_not_called()
            path.unlink()
            self.write_key('sk-offline')

    def test_setup_preflights_node_and_only_pauses_on_tty(self):
        self.write_config()
        self.m.NODE = None
        with mock.patch.object(self.m, 'initialize'), \
                mock.patch.object(self.m.getpass, 'getpass') as prompt, \
                mock.patch.object(self.m, 'request') as request:
            with self.assertRaisesRegex(RuntimeError, 'Node.js'):
                self.m.main(['setup'])
            prompt.assert_not_called()
            request.assert_not_called()
        self.m.NODE = '/fake/node'
        self.write_key('sk-offline')
        for tty in (False, True):
            with self.subTest(tty=tty), mock.patch.object(self.m, 'initialize'), \
                    mock.patch.object(self.m, 'start', return_value=(self.state, {}, self.cfg)), \
                    mock.patch.object(self.m, 'smoke'), mock.patch.object(self.m.sys.stdin, 'isatty', return_value=tty), \
                    mock.patch('builtins.input') as pause, contextlib.redirect_stdout(io.StringIO()):
                self.m.main(['setup'])
                self.assertEqual(pause.call_count, int(tty))

    def test_launch_arguments_are_forwarded_and_help_escape_is_documented(self):
        for args, expected in (([], []), (['launch'], []),
                               (['launch', '--model', 'custom', 'prompt'], ['--model', 'custom', 'prompt']),
                               (['launch', '--', '--help'], ['--help'])):
            with self.subTest(args=args), mock.patch.object(self.m, 'initialize') as initialize, \
                    mock.patch.object(self.m, 'launch') as launch:
                self.m.main(args)
                initialize.assert_called_once_with()
                launch.assert_called_once_with(expected)

    def test_restart_invalid_preflight_never_stops(self):
        for failure in ('config', 'key', 'node'):
            self.write_config()
            self.write_key('sk-offline')
            self.m.NODE = '/fake/node'
            if failure == 'config':
                self.write_config(dict(self.cfg, model='YOUR_RESPONSES_MODEL_ID'))
            elif failure == 'key':
                self.write_key('invalid')
            else:
                self.m.NODE = None
            with self.subTest(failure=failure), mock.patch.object(self.m, 'initialize'), \
                    mock.patch.object(self.m, 'stop') as stop, mock.patch.object(self.m, 'start') as start:
                with self.assertRaises(RuntimeError):
                    self.m.main(['restart'])
                stop.assert_not_called()
                start.assert_not_called()


class RealProcessExitTests(unittest.TestCase):
    """Use only owned temporary Python children; no gateway, API key, or network."""

    def check_stop(self, pid):
        m = load_module()
        with tempfile.TemporaryDirectory() as directory:
            m.PRIVATE = pathlib.Path(directory)
            state = {'pid': pid, 'url': 'http://127.0.0.1:18789',
                     'token': 'offline-only', 'instance_id': 'offline-only',
                     'config_fingerprint': 'offline-only'}
            m.save(m.PRIVATE / 'state.json', state)
            m.STOP_ATTEMPTS = 30
            with mock.patch.object(m, 'health', return_value={'ok': True}):
                m.stop()
            self.assertFalse((m.PRIVATE / 'state.json').exists())

    def test_stop_recognizes_own_exited_child(self):
        child = subprocess.Popen([os.sys.executable, '-c',
            'import time; print("ready", flush=True); time.sleep(60)'],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            self.check_stop(child.pid)
        finally:
            if child.poll() is None: child.terminate()
            child.wait(timeout=5)
            child.stdout.close()

    def test_stop_recognizes_zombie_owned_by_another_parent(self):
        # Simulate setup's still-open terminal: parent remains alive after its Node exits.
        parent = subprocess.Popen([os.sys.executable, '-c',
            'import subprocess,sys; child=subprocess.Popen([sys.executable,"-c",'
            '"import time; time.sleep(60)"]); print(child.pid,flush=True); '
            'sys.stdin.readline(); child.terminate() if child.poll() is None else None; child.wait()'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            pid = int(parent.stdout.readline().strip())
            self.check_stop(pid)
        finally:
            parent.stdin.write('reap\n'); parent.stdin.flush(); parent.stdin.close()
            parent.wait(timeout=5)
            parent.stdout.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
