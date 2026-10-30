#!/usr/bin/env python3
import getpass, json, os, pathlib, secrets, shutil, socket, subprocess, sys, time, urllib.request, urllib.error
ROOT = pathlib.Path(__file__).resolve().parent
PRIVATE = ROOT / 'private'
PRIVATE.mkdir(mode=0o700, exist_ok=True)
os.chmod(PRIVATE, 0o700)
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

NODE = find_node()
CLAUDE = os.environ.get('CLAUDE_OPENAI_CLI') or shutil.which('claude')
if not CLAUDE and NODE:
    sibling = pathlib.Path(NODE).parent/'claude'
    if sibling.exists(): CLAUDE=str(sibling)
if not (ROOT/'config.json').exists():
    shutil.copyfile(ROOT/'config.example.json', ROOT/'config.json')

def save(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)

def request(url, key=None, data=None):
    headers = {'Content-Type':'application/json'}
    if key: headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        proxy=json.loads((ROOT/'config.json').read_text()).get('https_proxy')
        proxies={'https':proxy} if proxy and url.startswith('https://api.openai.com/') else {}
        opener=urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        with opener.open(req, timeout=120) as r: return json.load(r)
    except urllib.error.HTTPError as e:
        try: detail = json.load(e).get('error', {})
        except Exception: detail = {}
        # Only error classification is shown; never print request headers or keys.
        raise RuntimeError(f'HTTP {e.code}: {detail.get("code") or detail.get("type") or "request_failed"}') from None

def credentials():
    path = PRIVATE / 'credentials.json'
    if not path.exists():
        print('尚未保存 API Key，请先运行 setup.command。')
        sys.exit(1)
    return json.loads(path.read_text())

def start():
    if not NODE: raise RuntimeError('未找到 Node.js，请安装 Node.js 24+ 或设置 CLAUDE_OPENAI_NODE')
    cred = credentials()
    cfg = json.loads((ROOT / 'config.json').read_text())
    statepath = PRIVATE / 'state.json'
    if statepath.exists():
        state = json.loads(statepath.read_text())
        try:
            h = request(state['url'] + '/health')
            if h.get('big') == cfg['model'] and h.get('backend') == 'openai-responses' and state['url'] == f"http://127.0.0.1:{cfg.get('port',18789)}":
                return state, cred, cfg
        except Exception: pass
    port=int(cfg.get('port',18789))
    tokenpath=PRIVATE/'gateway-token.json'
    if tokenpath.exists(): token=json.loads(tokenpath.read_text())['token']
    else:
        token=secrets.token_urlsafe(32); save(tokenpath,{'token':token})
    env=os.environ.copy()
    env.update(OPENAI_API_KEY=cred['api_key'], GPT_PROXY_TOKEN=token, GPT_PROXY_MODEL=cfg['model'], GPT_PROXY_SMALL_MODEL=cfg['model'], GPT_PROXY_REASONING=cfg['reasoning'], GPT_PROXY_PORT=str(port), GPT_PROXY_DEBUG='0')
    if cfg.get('https_proxy'):
        env.update(HTTPS_PROXY=cfg['https_proxy'], NODE_USE_ENV_PROXY='1', NO_PROXY='127.0.0.1,localhost')
    logpath=PRIVATE/'gateway.log'
    fd=os.open(logpath,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'a') as log:
        proc=subprocess.Popen([NODE,str(ROOT/'src/server.js')],env=env,stdout=log,stderr=log,start_new_session=True)
    state={'pid':proc.pid,'url':f'http://127.0.0.1:{port}','token':token}
    for _ in range(50):
        try:
            request(state['url']+'/health'); save(statepath,state); return state,cred,cfg
        except Exception:
            if proc.poll() is not None: raise RuntimeError('网关启动失败，请检查 private/gateway.log')
            time.sleep(.1)
    proc.terminate()
    raise RuntimeError('网关启动超时')

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
    with urllib.request.urlopen(req,timeout=120) as result: stream=result.read().decode()
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

try:
    mode=sys.argv[1] if len(sys.argv)>1 else 'launch'
    if mode=='setup':
        print('Claude Code → 本地网关 → OpenAI 官方 API\n密钥仅保存本机 private/credentials.json（权限 600），不会修改原来的 Claude 配置。')
        if (PRIVATE/'credentials.json').exists():
            print('使用已保存的 Key。需要更换时先删除 private/credentials.json，再运行 setup.command。')
        else:
            key=getpass.getpass('粘贴 OpenAI API Key 后按回车（输入不显示）：').strip()
            if not key.startswith('sk-') or any(c.isspace() for c in key): raise RuntimeError('Key 格式不正确')
            print('验证 Key 与模型权限…',flush=True)
            models=request('https://api.openai.com/v1/models',key)
            ids={m['id'] for m in models['data']}
            cfg=json.loads((ROOT/'config.json').read_text())
            if cfg['model'] not in ids:
                print('默认模型不可用，请输入账号可用的 GPT 模型 ID。')
                chosen=input('模型 ID：').strip()
                if chosen not in ids or not chosen.startswith('gpt-'): raise RuntimeError('模型不可用')
                cfg['model']=chosen; save(ROOT/'config.json',cfg)
            save(PRIVATE/'credentials.json',{'api_key':key})
        state,cred,cfg=start(); smoke(state,cfg)
        print('\n配置完成。以后运行 claude-openai/launch.command 启动 GPT 版 Claude Code。\n原来的 claude 命令仍使用原配置。')
        input('\n按回车关闭此窗口…')
    elif mode=='desktop':
        state,cred,cfg=start()
        subprocess.run(['/usr/bin/open','-a','/Applications/Claude.app'],check=True)
        print('Gateway ready: '+state['url'])
    elif mode=='start':
        state,cred,cfg=start(); print('Gateway ready: '+state['url'])
    elif mode=='verify':
        state,cred,cfg=start(); smoke(state,cfg)
    elif mode=='stop':
        p=PRIVATE/'state.json'
        if p.exists():
            state=json.loads(p.read_text())
            try:
                h=request(state['url']+'/health')
                if h.get('backend')=='openai-responses': os.kill(state['pid'],15)
            except Exception: pass
            p.unlink(missing_ok=True)
        print('网关已停止。')
    else: launch(sys.argv[2:])
except (Exception,KeyboardInterrupt) as e:
    print('\n未完成：'+str(e),flush=True)
    if sys.stdin.isatty(): input('按回车关闭…')
    sys.exit(1)
