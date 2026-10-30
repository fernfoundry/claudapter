const assert = require('node:assert/strict');
process.env.OPENAI_API_KEY='sk-mock-key-not-real';
const backend=require('../src/responses-backend');
let requests=[];
let round=0;
global.fetch=async(url,opts)=>{
 assert.equal(url,'https://api.openai.com/v1/responses');
 assert.equal(opts.headers.authorization,'Bearer sk-mock-key-not-real');
 const payload=JSON.parse(opts.body); requests.push(payload);
 assert(payload.max_output_tokens >= 16);
 assert.equal(payload.parallel_tool_calls,false);
 assert.equal(payload.store,false);
 const events=round++===0?[
  {type:'response.output_item.added',output_index:0,item:{type:'function_call',id:'fc_1',call_id:'call_1',name:'Read',arguments:''}},
  {type:'response.function_call_arguments.delta',item_id:'fc_1',delta:'{"file_path":"test.txt"}'},
  {type:'response.completed',response:{status:'completed',usage:{input_tokens:100,output_tokens:20}}}
 ]:[{type:'response.output_text.delta',delta:'OK'}, {type:'response.completed',response:{status:'completed',usage:{input_tokens:110,output_tokens:10}}}];
 return new Response(events.map(e=>'data: '+JSON.stringify(e)+'\n\n').join(''),{headers:{'Content-Type':'text/event-stream'}});
};
const calls=[];
const writer={toolStart:(...x)=>calls.push(['start',...x]),toolArgs:(...x)=>calls.push(['args',...x]),text:x=>calls.push(['text',x])};
(async()=>{
 const body={system:[{type:'text',text:'coding assistant'}],model:'gpt-6.1-sol',max_tokens:2048,messages:[{role:'user',content:'read test.txt'}],tools:[{name:'Read',input_schema:{type:'object',properties:{file_path:{type:'string'}}}}],tool_choice:{type:'tool',name:'Read'}};
 await backend.run(body,body.model,'medium',writer,()=>{});
 assert.deepEqual(calls[0],['start','fc_1','call_1','Read']);
 assert.equal(requests[0].tools[0].strict,false);
 assert.equal(requests[0].max_output_tokens,2048);
 const tiny={...body,max_tokens:1};
 await backend.run(tiny,body.model,'medium',writer,()=>{});
 assert.equal(requests[1].max_output_tokens,16);
 body.messages.push({role:'assistant',content:[{type:'tool_use',id:'call_1',name:'Read',input:{file_path:'test.txt'}}]}, {role:'user',content:[{type:'tool_result',tool_use_id:'call_1',content:'hello'}]});
 await backend.run(body,body.model,'medium',writer,()=>{});
 assert.equal(requests[2].input[1].type,'function_call');
 assert.equal(requests[2].input[2].type,'function_call_output');
 assert.equal(requests[2].input[2].call_id,'call_1');
 assert.equal(requests[2].input[2].output,'hello');
 assert.deepEqual(calls.at(-1),['text','OK']);
 global.fetch=async()=>new Response('data: '+JSON.stringify({type:'response.output_text.delta',delta:'partial'})+'\n\n');
 await assert.rejects(backend.run(body,body.model,'medium',writer,()=>{}),/before completion/);
 console.log('PASS: official endpoint, tool schema, tool call/result IDs, text, usage, truncated-stream failure');
})().catch(e=>{console.error(e);process.exit(1)});
