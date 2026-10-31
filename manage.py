#!/usr/bin/env python3
import getpass, hashlib, hmac, http.client, json, os, pathlib, re, secrets, shutil, signal, socket, subprocess, sys, time, urllib.parse, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).resolve().parent
PRIVATE = ROOT / 'private'
NODE = None
CLAUDE = None
DESKTOP_APP = pathlib.Path('/Applications/Claude.app')
SERVICE = 'claude-openai-gateway'
HEALTH_TIMEOUT = 0.4
START_ATTEMPTS = 50
STOP_ATTEMPTS = 50
PROXY_ENV = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'NODE_USE_ENV_PROXY')

def initialize():
    """Only the CLI initializes local files or probes installed runtimes."""
    global NODE, CLAUDE
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    NODE = find_node()
    CLAUDE = os.environ.get('CLAUDE_OPENAI_CLI') or shutil.which('claude')
    if not CLAUDE and NODE:
        sibling = pathlib.Path(NODE).parent / 'claude'
        if sibling.exists(): CLAUDE = str(sibling)

def find_node():
    explicit = os.environ.get('CLAUDE_OPENAI_NODE')
    candidates = [explicit] if explicit else [shutil.which('node')]
    nvm = pathlib.Path.home()/'.nvm/versions/node'
    candidates += sorted((str(p) for p in nvm.glob('*/bin/node')), reverse=True)
    for candidate in candidates:
        if not candidate: continue
        try:
            version = subprocess.check_output([candidate,'--version'],text=True,timeout=5).strip()
            if int(version.lstrip('v').split('.')[0]) >= 24: return candidate
        except (OSError, ValueError, subprocess.SubprocessError): pass
    return None

def local_url(url):
    if not isinstance(url, str) or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', url):
        raise RuntimeError('state URL 必须为 http://127.0.0.1:端口；拒绝发送 token')
    port = int(url.rsplit(':', 1)[1])
    if not 1 <= port <= 65535:
        raise RuntimeError('state 端口无效')
    return port


def validate_state(state):
    if not isinstance(state, dict): raise RuntimeError('未知 state，拒绝操作')
    local_url(state.get('url'))
    if (type(state.get('pid')) is not int or state['pid'] <= 1
            or any(not isinstance(state.get(k), str) or not state[k]
                   for k in ('token', 'instance_id', 'config_fingerprint'))):
        raise RuntimeError('未知或旧版 state，拒绝操作；请手动确认原进程身份')
    return state


def health(state):
    validate_state(state)
    reply = request(state['url'] + '/health', state['token'])
    if (not isinstance(reply, dict) or reply.get('service') != SERVICE
            or type(reply.get('pid')) is not int or reply['pid'] != state['pid']
            or reply.get('instance_id') != state['instance_id'] or reply.get('ok') is not True):
        raise RuntimeError('网关身份不匹配，拒绝复用或终止')
    return reply

def save(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)

def request(url, key=None, data=None):
    if url.endswith('/health'):
        port = local_url(url[:-len('/health')])
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=HEALTH_TIMEOUT)
        try:
            conn.request('GET', '/health', headers={'Authorization': 'Bearer ' + (key or '')})
            reply = conn.getresponse()
            if reply.status != 200: raise RuntimeError(f'Health HTTP {reply.status}')
            raw = reply.read(65537)
            if len(raw) > 65536: raise RuntimeError('Health response too large')
            return json.loads(raw)
        finally:
            conn.close()
    cfg = load_config()
    headers = {'Content-Type':'application/json'}
    if key: headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        proxy=cfg['https_proxy']
        proxies={'https':proxy} if proxy and url.startswith('https://api.openai.com/') else {}
        opener=urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        with opener.open(req, timeout=120) as r: return json.load(r)
    except urllib.error.HTTPError as e:
        try: detail = json.load(e).get('error', {})
        except Exception: detail = {}
        # Only error classification is shown; never print request headers or keys.
        raise RuntimeError(f'HTTP {e.code}: {detail.get("code") or detail.get("type") or "request_failed"}') from None

def read_object(path):
    """Report file/JSON errors without echoing potentially private values."""
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise RuntimeError(f'缺少 {path.name}；请先按 README 完成配置') from None
    except (ValueError, UnicodeError):
        raise RuntimeError(f'{path.name} 不是有效 JSON；请检查引号、逗号与编码') from None
    except OSError:
        raise RuntimeError(f'无法读取 {path.name}；请检查文件权限') from None
    if not isinstance(value, dict):
        raise RuntimeError(f'{path.name} 顶层必须是 JSON 对象')
    return value


def load_config():
    return validate_config(read_object(ROOT / 'config.json'))


def validate_config(cfg):
    for field in ('model', 'reasoning'):
        value = cfg.get(field)
        if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
            raise RuntimeError(f'config.json 的 {field} 必须是非空、无空白字符串')
    if cfg['model'] == 'YOUR_RESPONSES_MODEL_ID':
        raise RuntimeError('请将 config.json 的 model 占位符替换为实际可用的模型 ID')
    port = cfg.get('port', 18789)
    if type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError('config.json 的 port 必须是 1–65535 的整数')
    proxy = cfg.get('https_proxy', '')
    if not isinstance(proxy, str):
        raise RuntimeError('config.json 的 https_proxy 必须是字符串；直连请填空字符串')
    if proxy:
        try:
            parsed = urllib.parse.urlsplit(proxy)
            valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                     and parsed.port is not None and 1 <= parsed.port <= 65535
                     and not parsed.query and not parsed.fragment
                     and parsed.path in ('', '/') and not any(c.isspace() for c in proxy))
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError('config.json 的 https_proxy 需为 http(s)://主机:端口；直连请填空字符串')
    return dict(cfg, port=port, https_proxy=proxy)


def valid_api_key(key):
    return (isinstance(key, str) and key.startswith('sk-') and len(key) > 3
            and not any(c.isspace() or not c.isprintable() for c in key))


def credentials():
    path = PRIVATE / 'credentials.json'
    if not path.exists():
        raise RuntimeError('尚未保存 API Key；请运行 python3 manage.py setup')
    cred = read_object(path)
    key = cred.get('api_key')
    if not valid_api_key(key):
        raise RuntimeError('credentials.json 中的 api_key 无效；请重新配置，切勿分享该文件')
    return cred

def child_environment(cred, cfg, token, instance_id):
    env = os.environ.copy()
    # Clear every inherited proxy spelling before selecting explicit proxy or direct mode.
    for name in PROXY_ENV: env.pop(name, None)
    env.update(OPENAI_API_KEY=cred['api_key'], GPT_PROXY_TOKEN=token,
               GPT_PROXY_INSTANCE=instance_id, GPT_PROXY_MODEL=cfg['model'],
               GPT_PROXY_SMALL_MODEL=cfg['model'], GPT_PROXY_REASONING=cfg['reasoning'],
               GPT_PROXY_PORT=str(int(cfg.get('port', 18789))), GPT_PROXY_DEBUG='0')
    if cfg.get('https_proxy'):
        env.update(HTTPS_PROXY=cfg['https_proxy'], NODE_USE_ENV_PROXY='1',
                   NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    return env


def config_fingerprint(cred, cfg, token):
    # HMAC the whole description, including key and inherited environment; no raw key is persisted.
    inherited = {k: v for k, v in os.environ.items()
                 if k.upper().endswith('_PROXY') or k.startswith(('NODE_', 'OPENSSL_'))
                 or k in ('PATH', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((ROOT / 'src').rglob('*.js'))}
    description = {'api_key': cred['api_key'], 'model': cfg['model'],
                   'reasoning': cfg['reasoning'], 'port': int(cfg.get('port', 18789)),
                   'proxy': cfg.get('https_proxy') or '', 'node': str(pathlib.Path(NODE).resolve()),
                   'sources': sources, 'inherited': inherited,
                   'effective_env': child_environment(cred, cfg, token, '<instance>')}
    # Only fingerprint proxy/runtime-relevant inherited values, not unrelated changing shell variables.
    description['effective_env'] = {k: v for k, v in description['effective_env'].items()
                                    if k in inherited or k.startswith('GPT_PROXY_') or k == 'OPENAI_API_KEY'}
    raw = json.dumps(description, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()


def pid_exists(pid):
    # Reap our own exited child if this process also launched the gateway.
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
        if waited == pid: return False
    except ChildProcessError:
        pass  # Another process owns this PID; do not try to reap it.
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    return True


def is_zombie(pid):
    """An exited process may await its original parent (for example an open setup terminal)."""
    try:
        if sys.platform.startswith('linux'):
            status = pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        else:
            status = subprocess.check_output(
                ['/bin/ps', '-o', 'stat=', '-p', str(pid)],
                text=True, stderr=subprocess.DEVNULL, timeout=1).strip()
        return status.startswith('Z')
    except (OSError, IndexError, subprocess.SubprocessError):
        return False  # Uncertainty must not be treated as proof of exit.


def require_node():
    if not NODE: raise RuntimeError('未找到 Node.js，请安装 Node.js 24+ 或设置 CLAUDE_OPENAI_NODE')


def preflight():
    cfg = load_config()
    cred = credentials()
    require_node()
    return cred, cfg


def start():
    cred, cfg = preflight()
    statepath = PRIVATE / 'state.json'
    if statepath.exists():
        state = validate_state(read_object(statepath))
        health(state)  # Failure is deliberately not interpreted as permission to replace a process.
        if not pid_exists(state['pid']): raise RuntimeError('state PID 不存在，拒绝复用')
        if state['config_fingerprint'] != config_fingerprint(cred, cfg, state['token']):
            raise RuntimeError('配置、密钥、源码或环境已变化；请明确运行 restart')
        return state, cred, cfg
    port = int(cfg.get('port', 18789))
    url = f'http://127.0.0.1:{port}'
    local_url(url)
    # No health probe may adopt an unrelated listener, even if it looks like this service.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Match Node's restart behavior: TIME_WAIT is not a live listener.
        # Do not use SO_REUSEPORT; an existing listener must still be rejected.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: probe.bind(('127.0.0.1', port))
        except OSError as e: raise RuntimeError(f'端口 {port} 已占用；拒绝启动') from e
    tokenpath = PRIVATE / 'gateway-token.json'
    if tokenpath.exists(): token = read_object(tokenpath).get('token')
    else:
        token = secrets.token_urlsafe(32)
        save(tokenpath, {'token': token})
    if not isinstance(token, str) or not token: raise RuntimeError('本地 token 无效')
    instance_id = secrets.token_urlsafe(32)
    state = {'url': url, 'token': token, 'instance_id': instance_id,
             'config_fingerprint': config_fingerprint(cred, cfg, token)}
    env = child_environment(cred, cfg, token, instance_id)
    fd = os.open(PRIVATE / 'gateway.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as log:
        proc = subprocess.Popen([NODE, str(ROOT / 'src/server.js')], env=env,
                                stdout=log, stderr=log, start_new_session=True)
    state['pid'] = proc.pid
    try:
        for _ in range(START_ATTEMPTS):
            if proc.poll() is not None: raise RuntimeError('网关启动失败，请检查 private/gateway.log')
            try: health(state)
            except Exception: pass
            else:
                if proc.poll() is not None: raise RuntimeError('网关启动后立即退出')
                save(statepath, state)
                return state, cred, cfg
            time.sleep(.1)
        raise RuntimeError('网关启动超时；未保存 state')
    except BaseException:
        # This handle is our own spawned child, not an unverified PID read from disk.
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        raise


def stop():
    statepath = PRIVATE / 'state.json'
    if not statepath.exists(): return
    state = validate_state(read_object(statepath))
    health(state)
    if not pid_exists(state['pid']): raise RuntimeError('state PID 不存在；保留 state')
    os.kill(state['pid'], signal.SIGTERM)
    for _ in range(STOP_ATTEMPTS):
        if not pid_exists(state['pid']) or is_zombie(state['pid']):
            statepath.unlink()
            return
        time.sleep(.1)
    raise RuntimeError('未确认网关终止；保留 state，不强制终止未知进程')


def status():
    """Read only stored identity and authenticated loopback health, never configuration."""
    try:
        statepath = PRIVATE / 'state.json'
        if not statepath.exists():
            print('未受管理：没有本地 state；未检查端口是否空闲。')
            return
        state = validate_state(read_object(statepath))
        health(state)
        if not pid_exists(state['pid']):
            raise RuntimeError('missing PID')
    except Exception:
        # File contents, server responses and OS errors may contain secrets.
        raise RuntimeError('无法确认网关状态：请检查本地 state、进程与健康检查；未修改任何文件。') from None
    print(f"网关运行正常：{state['url']} PID {state['pid']}")


def restart():
    preflight()  # Never stop a verified instance for an invalid replacement configuration.
    stop()
    return start()

def smoke(state,cfg):
    url=state['url']+'/v1/messages'; key=state['token']
    print('正在测试官方 API 与工具调用（会消耗少量 API 额度）…',flush=True)
    body={'model':cfg['model'],'max_tokens':2048,'stream':False,
      'messages':[{'role':'user','content':'Call gateway_check with value ok.'}],
      'tools':[{'name':'gateway_check','description':'Gateway connectivity check','input_schema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}],
      'tool_choice':{'type':'tool','name':'gateway_check'}}
    response=request(url,key,body)
    tool=next((b for b in response.get('content',[]) if b.get('type')=='tool_use'),None)
    if not tool or tool['name']!='gateway_check' or tool['input'].get('value')!='ok':
        raise RuntimeError('工具调用校验失败')
    body['messages'] += [{'role':'assistant','content':response['content']},{'role':'user','content':[{'type':'tool_result','tool_use_id':tool['id'],'content':'GATEWAY_CHECK_OK'},{'type':'text','text':'Return only GATEWAY_CHECK_OK.'}]}]
    body['tool_choice']={'type':'none'}
    response=request(url,key,body)
    answer=''.join(b.get('text','') for b in response.get('content',[]))
    if 'GATEWAY_CHECK_OK' not in answer: raise RuntimeError('工具结果回传校验失败')
    body['messages']=[{'role':'user','content':'Return exactly STREAM_OK.'}]
    body.pop('tools',None); body.pop('tool_choice',None); body['stream']=True
    req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req,timeout=120) as result: stream=result.read().decode()
    text=''.join(json.loads(line[6:]).get('delta',{}).get('text','') for line in stream.splitlines() if line.startswith('data: '))
    if 'event: error' in stream or 'event: message_stop' not in stream or 'STREAM_OK' not in text:
        raise RuntimeError('流式输出校验失败')
    save(PRIVATE/'verification.json' ,{'ok':True,'model':cfg['model'],'checked_at':time.time(),'checks':['official_responses','tool_call','tool_result','streaming']})
    print('测试通过：官方 Responses API、工具调用、工具结果回传、流式输出。',flush=True)

def launch(args):
    if not CLAUDE: raise RuntimeError('未找到 claude 命令，请安装 Claude Code 或设置 CLAUDE_OPENAI_CLI')
    state,cred,cfg=start()
    override={'env':{
        'ANTHROPIC_BASE_URL':state['url'],'ANTHROPIC_AUTH_TOKEN':state['token'],'ANTHROPIC_API_KEY':'',
        'ANTHROPIC_MODEL':cfg['model'],'ANTHROPIC_SMALL_FAST_MODEL':cfg['model'],
        'ANTHROPIC_DEFAULT_OPUS_MODEL':cfg['model'],'ANTHROPIC_DEFAULT_SONNET_MODEL':cfg['model'],'ANTHROPIC_DEFAULT_HAIKU_MODEL':cfg['model'],
        'CLAUDE_CODE_USE_BEDROCK':'0','CLAUDE_CODE_USE_VERTEX':'0','CLAUDE_CODE_USE_FOUNDRY':'0'},'model':cfg['model']}
    settings=PRIVATE/'claude-settings.json'; save(settings,override)
    env=os.environ.copy()
    env['PATH']=str(pathlib.Path(NODE).parent)+os.pathsep+env.get('PATH','')
    env.pop('OPENAI_API_KEY',None)
    env.update(override['env'])
    command=[CLAUDE,'--settings',str(settings),'--model',cfg['model'],*args]
    os.execve(CLAUDE,command,env)

def configure_credentials(cfg):
    print('Claude Code → 本地网关 → OpenAI 官方 API\n密钥仅保存本机 private/credentials.json（权限 600），不会修改原来的 Claude 配置。')
    if (PRIVATE/'credentials.json').exists():
        print('使用已保存的 Key。需要更换时先删除 private/credentials.json，再运行 setup.command。')
    else:
        key=getpass.getpass('粘贴 OpenAI API Key 后按回车（输入不显示）：')
        if not valid_api_key(key): raise RuntimeError('Key 格式不正确')
        print('验证 Key 与模型权限…',flush=True)
        models=request('https://api.openai.com/v1/models',key)
        ids={m['id'] for m in models['data']}
        if cfg['model'] not in ids:
            print('默认模型不可用，请输入账号可用的 GPT 模型 ID。')
            chosen=input('模型 ID：').strip()
            if chosen not in ids or not chosen.startswith('gpt-'): raise RuntimeError('模型不可用')
            cfg['model']=chosen; save(ROOT/'config.json',cfg)
        save(PRIVATE/'credentials.json',{'api_key':key})

def guided_config():
    path = ROOT / 'config.json'
    if path.exists():
        return load_config()
    defaults = validate_config(read_object(ROOT / 'config.example.json'))
    print('首次使用 Claudapter，先做几个简单设置。回车使用括号中的默认值。')
    model = input(f"OpenAI 模型 [{defaults['model']}]：").strip() or defaults['model']
    effort = input(f"Reasoning effort [{defaults['reasoning']}]：").strip() or defaults['reasoning']
    proxy = input('HTTP(S) 代理地址 [留空直连]：').strip()
    cfg = validate_config(dict(defaults, model=model, reasoning=effort, https_proxy=proxy))
    save(path, cfg)
    return cfg


def onboard():
    require_node()
    missing = not (ROOT / 'config.json').exists() or not (PRIVATE / 'credentials.json').exists()
    if (missing or (PRIVATE / 'setup-pending.json').exists()) and not sys.stdin.isatty():
        raise RuntimeError('首次配置需要交互终端。请在终端运行 ./claudapter setup；不会从管道读取 API Key')
    cfg = guided_config()
    configure_credentials(cfg)
    print('接下来验证真实 API，会产生少量用量。', flush=True)
    pending = PRIVATE / 'setup-pending.json'
    save(pending, {'pending': True})
    state, cred, cfg = start()
    smoke(state, cfg)
    pending.unlink()
    print('配置完成。以后运行 ./claudapter 即可启动，桌面端用 ./claudapter desktop。')
    return state, cred, cfg


def connect(args=None, desktop=False):
    require_node()
    if desktop:
        if sys.platform != 'darwin' or not DESKTOP_APP.exists():
            raise RuntimeError('桌面入口需要 macOS 和 /Applications/Claude.app')
    elif not CLAUDE:
        raise RuntimeError('未找到 claude 命令。请先安装 Claude Code，或设置 CLAUDE_OPENAI_CLI')
    if (not (ROOT / 'config.json').exists() or not (PRIVATE / 'credentials.json').exists()
            or (PRIVATE / 'setup-pending.json').exists()):
        onboard()
    if desktop:
        state, _, _ = start()
        subprocess.run(['/usr/bin/open', '-a', '/Applications/Claude.app'], check=True)
        print('Gateway ready: ' + state['url'])
    else:
        launch(args or [])


MODES = ('onboard', 'connect', 'connect-desktop', 'setup', 'desktop', 'start', 'verify', 'stop', 'restart', 'status', 'launch')


def show_help():
    print('''用法：python3 manage.py [命令]
  onboard   引导配置模型、代理和 Key，再验证 API
  connect   首次自动引导配置，然后启动 Claude Code
  connect-desktop   首次自动配置，然后打开 macOS 桌面端
  setup     配置 Key 并验证模型（会调用付费 API）
  launch    启动网关与 Claude Code（默认命令）
  desktop   启动网关后打开 Claude Desktop
  start     启动或复用已验证的网关
  verify    验证 API、工具调用与流式输出（会消耗 API 额度）
  status    只读检查已保存身份、本地认证健康检查与 PID，不调用 OpenAI
  stop      仅在身份验证通过后停止网关；不依赖 config 或 Node.js
  restart   先检查配置、Key 与 Node.js，再停止并启动网关
  help, -h, --help   显示帮助，不创建文件或探测运行时
命令后单独的 -h/--help 也显示此帮助，包括 launch --help。
launch [参数...] 将参数传给 Claude Code；launch -- --help 转发 Claude 自身帮助。
status 无 state 时仅表示未受管理，不表示端口空闲。''')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else 'launch'
    extra = args[1:]
    if mode in ('help', '-h', '--help'):
        if extra and not (mode == 'help' and len(extra) == 1 and extra[0] in MODES):
            raise RuntimeError('帮助命令参数无效；请运行 python3 manage.py --help')
        show_help()
        return
    if mode not in MODES:
        raise RuntimeError('未知 mode；请运行 python3 manage.py --help')
    if extra in (['-h'], ['--help']):
        show_help()
        return
    if mode not in ('launch', 'connect') and extra:
        raise RuntimeError('此命令不接受额外参数；请运行 python3 manage.py --help')
    if mode == 'status':
        status()
        return
    if mode == 'stop':
        stop()
        print('网关已停止。')
        return
    initialize()
    if mode=='onboard':
        onboard()
    elif mode=='connect':
        connect(extra[1:] if extra[:1] == ['--'] else extra)
    elif mode=='connect-desktop':
        connect(desktop=True)
    elif mode=='setup':
        cfg = load_config()
        require_node()
        configure_credentials(cfg)
        state,cred,cfg=start(); smoke(state,cfg)
        print('\n配置完成。运行本仓库的 launch.command 或 python3 manage.py launch 启动。\n原来的 claude 命令仍使用原配置。')
        if sys.stdin.isatty(): input('\n按回车关闭此窗口…')
    elif mode=='desktop':
        state,cred,cfg=start()
        subprocess.run(['/usr/bin/open','-a','/Applications/Claude.app'],check=True)
        print('Gateway ready: '+state['url'])
    elif mode=='start':
        state,cred,cfg=start(); print('Gateway ready: '+state['url'])
    elif mode=='verify':
        state,cred,cfg=start(); smoke(state,cfg)
    elif mode=='restart':
        state,cred,cfg=restart(); print('Gateway ready: '+state['url'])
    else: launch(extra[1:] if extra[:1] == ['--'] else extra)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as e:
        print('\n未完成：'+str(e), flush=True)
        if sys.stdin.isatty(): input('按回车关闭…')
        sys.exit(1)
