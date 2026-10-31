"""First-run/returning-user flows; fake API, process and client, temporary files only."""
import contextlib, importlib.machinery, importlib.util, io, json, pathlib, sys, tempfile, unittest
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parents[1]

def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

class OnboardingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.m = load(ROOT/'manage.py', 'manage_onboarding_test')
        self.m.ROOT = pathlib.Path(temp.name); self.m.PRIVATE = self.m.ROOT/'private'
        self.m.PRIVATE.mkdir(); self.m.NODE = '/fake/node'; self.m.CLAUDE = '/fake/claude'
        self.cfg = {'model':'gpt-offline','reasoning':'medium','https_proxy':'','port':18789}
        (self.m.ROOT/'config.example.json').write_text(json.dumps(self.cfg))
        with mock.patch.dict(sys.modules, {'manage':self.m}):
            self.entry = load(ROOT/'claudapter','entry_test')
        self.key='sk-offline-test-only'; self.state={'url':'http://127.0.0.1:18789'}
        self.initialize=mock.patch.object(self.m,'initialize').start()
        self.prompt=mock.patch('builtins.input',side_effect=['','','']).start()
        self.hidden=mock.patch.object(self.m.getpass,'getpass',return_value=self.key).start()
        self.request=mock.patch.object(self.m,'request',return_value={'data':[{'id':'gpt-offline'}]}).start()
        self.start=mock.patch.object(self.m,'start',return_value=(self.state,{},self.cfg)).start()
        self.smoke=mock.patch.object(self.m,'smoke').start()
        self.launch=mock.patch.object(self.m,'launch').start()
        self.tty=mock.patch.object(self.m.sys.stdin,'isatty',return_value=True).start()
        self.addCleanup(mock.patch.stopall)
    def saved(self):
        self.m.save(self.m.ROOT/'config.json',self.cfg)
        self.m.save(self.m.PRIVATE/'credentials.json',{'api_key':self.key})
    def call(self,args):
        with contextlib.redirect_stdout(io.StringIO()) as output: self.entry.main(args)
        self.assertNotIn(self.key,output.getvalue())
        return output.getvalue()
    def test_first_launch_configures_verifies_then_launches(self):
        self.call([])
        self.assertEqual(json.loads((self.m.ROOT/'config.json').read_text()),self.cfg)
        self.assertEqual(json.loads((self.m.PRIVATE/'credentials.json').read_text()),{'api_key':self.key})
        self.assertEqual(self.prompt.call_count,3); self.hidden.assert_called_once()
        self.request.assert_called_once_with('https://api.openai.com/v1/models',self.key)
        self.smoke.assert_called_once_with(self.state,self.cfg); self.launch.assert_called_once_with([])
        self.assertEqual((self.m.PRIVATE/'credentials.json').stat().st_mode & 0o777,0o600)
    def test_failed_first_check_is_retried_before_launch(self):
        self.smoke.side_effect=RuntimeError('offline failure')
        with self.assertRaisesRegex(RuntimeError,'offline failure'): self.call([])
        self.launch.assert_not_called()
        self.assertTrue((self.m.PRIVATE/'setup-pending.json').exists())
        self.tty.return_value=False
        with self.assertRaisesRegex(RuntimeError,'交互终端'): self.call([])
        self.assertEqual(self.smoke.call_count,1)
        self.tty.return_value=True; self.smoke.side_effect=None
        self.call([])
        self.assertFalse((self.m.PRIVATE/'setup-pending.json').exists())
        self.launch.assert_called_once_with([])
        self.hidden.assert_called_once()
        self.assertEqual(self.prompt.call_count,3)
    def test_custom_model_effort_proxy_are_saved(self):
        self.prompt.side_effect=['gpt-custom','high','http://127.0.0.1:7897']
        self.request.return_value={'data':[{'id':'gpt-custom'}]}
        self.call(['setup']); cfg=json.loads((self.m.ROOT/'config.json').read_text())
        self.assertEqual((cfg['model'],cfg['reasoning'],cfg['https_proxy']),('gpt-custom','high','http://127.0.0.1:7897'))
        self.launch.assert_not_called()
    def test_returning_user_preserves_config_and_skips_api_tests(self):
        self.saved(); before=(self.m.ROOT/'config.json').read_bytes()
        self.call(['-p','hello']); self.launch.assert_called_once_with(['-p','hello'])
        for mocked in [self.prompt,self.hidden,self.request,self.smoke]: mocked.assert_not_called()
        self.assertEqual((self.m.ROOT/'config.json').read_bytes(),before)
    def test_only_missing_key_does_not_reprompt_config(self):
        self.m.save(self.m.ROOT/'config.json',self.cfg); self.call(['setup'])
        self.prompt.assert_not_called(); self.hidden.assert_called_once()
    def test_non_tty_does_not_prompt_or_write_config(self):
        self.tty.return_value=False
        with self.assertRaisesRegex(RuntimeError,'交互终端'): self.call([])
        self.assertFalse((self.m.ROOT/'config.json').exists())
        for mocked in [self.prompt,self.hidden,self.request,self.launch]: mocked.assert_not_called()
    def test_missing_node_or_client_fails_before_onboarding(self):
        for field,pattern in [('NODE','Node.js'),('CLAUDE','claude')]:
            with self.subTest(field=field),mock.patch.object(self.m,field,None):
                with self.assertRaisesRegex(RuntimeError,pattern): self.call([])
        self.prompt.assert_not_called(); self.hidden.assert_not_called()
        self.assertFalse((self.m.ROOT/'config.json').exists())
    def test_invalid_proxy_is_not_saved(self):
        self.prompt.side_effect=['','','socks5://127.0.0.1:1234']
        with self.assertRaisesRegex(RuntimeError,'https_proxy'): self.call(['setup'])
        self.assertFalse((self.m.ROOT/'config.json').exists()); self.hidden.assert_not_called(); self.request.assert_not_called()
    def test_invalid_existing_config_is_not_overwritten(self):
        path=self.m.ROOT/'config.json';path.write_text('{broken')
        with self.assertRaisesRegex(RuntimeError,'有效 JSON'): self.call(['setup'])
        self.assertEqual(path.read_text(),'{broken'); self.prompt.assert_not_called(); self.hidden.assert_not_called()
    def test_help_and_unknown_command_have_no_side_effects(self):
        self.call(['--help'])
        with self.assertRaisesRegex(RuntimeError,'未知命令'): self.call(['statuz'])
        for mocked in [self.initialize,self.prompt,self.request]: mocked.assert_not_called()
    def test_wrong_desktop_platform_fails_before_key_prompt(self):
        with mock.patch.object(self.m.sys,'platform','linux'):
            with self.assertRaisesRegex(RuntimeError,'macOS'): self.call(['desktop'])
        self.prompt.assert_not_called();self.hidden.assert_not_called()
    def test_returning_desktop_opens_without_api_checks(self):
        self.saved()
        with mock.patch.object(self.m.sys,'platform','darwin'),mock.patch.object(self.m,'DESKTOP_APP') as app,mock.patch.object(self.m.subprocess,'run') as run:
            app.exists.return_value=True; self.call(['desktop'])
            run.assert_called_once_with(['/usr/bin/open','-a','/Applications/Claude.app'],check=True)
        for mocked in [self.request,self.smoke,self.prompt]: mocked.assert_not_called()

if __name__=='__main__': unittest.main(verbosity=2)
