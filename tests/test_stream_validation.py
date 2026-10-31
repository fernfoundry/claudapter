import ast, contextlib, io, json, pathlib, tempfile, types
source=(pathlib.Path(__file__).resolve().parents[1]/'manage.py').read_text()
function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='smoke')
code=ast.get_source_segment(source,function)
events=[('message_start',{'type':'message_start'}),('content_block_delta',{'delta':{'type':'text_delta','text':'STREAM'}}),('content_block_delta',{'delta':{'type':'text_delta','text':'_OK'}}),('message_stop',{'type':'message_stop'})]
stream=''.join('event: '+e+'\ndata: '+json.dumps(d)+'\n\n' for e,d in events)
class Reply:
 def __enter__(self): return self
 def __exit__(self,*args): pass
 def read(self): return stream.encode()
def run(function):
 proxy_handlers=[]
 replies=iter([{'content':[{'type':'tool_use','name':'gateway_check','id':'call_test','input':{'value':'ok'}}]},{'content':[{'type':'text','text':'GATEWAY_CHECK_OK'}]}])
 with tempfile.TemporaryDirectory() as temp:
  ns={'request':lambda *a:next(replies),'json':json,'urllib':types.SimpleNamespace(request=types.SimpleNamespace(Request=lambda *a,**k:None,urlopen=lambda *a,**k:(_ for _ in ()).throw(AssertionError('ambient proxy opener must not be used')),ProxyHandler=lambda proxies:proxy_handlers.append(proxies),build_opener=lambda *a:types.SimpleNamespace(open=lambda *a,**k:Reply()))),'PRIVATE':pathlib.Path(temp),'time':types.SimpleNamespace(time=lambda:0),'save':lambda *a:None}
  exec(function,ns)
  with contextlib.redirect_stdout(io.StringIO()): ns['smoke']({'url':'http://127.0.0.1:18789','token':'mock'},{'model':'mock'})
  assert proxy_handlers == [{}], 'stream verification must explicitly bypass ambient proxies'
legacy=code.replace("'STREAM_OK' not in text", "'STREAM_OK' not in stream")
try: run(legacy)
except RuntimeError as e: print('REPRODUCED legacy check:',e)
else: raise AssertionError('Reproduction must fail')
run(code)
print('PASS current check: split text is reassembled; loopback stream bypasses ambient proxies')
