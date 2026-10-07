'use strict';
const fs=require('node:fs');
const path=require('node:path');
const net=require('node:net');
const os=require('node:os');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const ROOT=path.join(process.env.XDG_STATE_HOME||path.join(os.homedir(),'.local/state'),'ai-command-terminal-layout');
const SLOT='__aiCommandTerminalLayoutV1';
function proc(pid){
  const base='/proc/'+pid, fields=fs.readFileSync(base+'/stat','utf8').split(') ').slice(1).join(') ').split(' ');
  return {pid:Number(pid),ppid:Number(fields[1]),start:fields[19],args:fs.readFileSync(base+'/cmdline','utf8').split('\0')};
}
function findHost(pid,appRoot){
  for(let depth=0;pid>1&&depth<12;depth++){
    const p=proc(pid);
    const remote=p.args.includes('--type=ptyHost');
    // Electron rewrites argv into a single process-title string and removes
    // its entrypoint environment variable. A terminal's Node utility parent
    // must belong to this exact VS Code application before it is considered.
    const utility=p.args.some(a=>a.includes('--utility-sub-type=node.mojom.NodeService'))&&
      path.join(path.dirname(fs.readlinkSync('/proc/'+pid+'/exe')),'resources/app')===appRoot;
    const isHost=remote||utility;
    if(isHost){
      const root=remote?path.dirname(fs.readlinkSync('/proc/'+pid+'/exe')):appRoot;
      const source=path.join(root,'out/vs/platform/terminal/node/ptyHostMain.js');
      return {...p,source,file:path.join(ROOT,`pty-${p.pid}-${p.start}.json`)};
    }
    pid=p.ppid;
  }
}
// Executed once inside the PTY host. It reads maps and writes only PID/layout
// metadata. No terminal method is replaced or called, and the timer is unref'd.
function publish(service,file,start){
  const fs=process.getBuiltinModule('node:fs');
  if(!(service?._workspaceLayoutInfos instanceof Map)||!(service?._ptys instanceof Map))throw Error('Unsupported PTY host');
  const previous=globalThis.__aiCommandTerminalLayoutV1;
  if(previous?.timer)clearInterval(previous.timer);
  const bridge={version:1};
  const write=()=>{
    try{
      const layouts=[...service._workspaceLayoutInfos].map(([workspaceId,layout])=>({workspaceId,tabs:layout.tabs.map(tab=>({
        pids:tab.terminals.map(entry=>service._ptys.get(typeof entry==='number'?entry:entry.terminal)?._pid||null),
        sizes:tab.terminals.map(entry=>typeof entry==='number'?0:entry.relativeSize)
      }))}));
      const snapshot={version:1,pid:process.pid,start,at:Date.now(),layouts};
      fs.writeFileSync(file+'.tmp',JSON.stringify(snapshot),{mode:0o600});fs.renameSync(file+'.tmp',file);
      bridge.error=undefined;
    }catch(error){bridge.error=error.message;}
  };
  bridge.timer=setInterval(write,500);bridge.timer.unref();bridge.dispose=()=>clearInterval(bridge.timer);
  globalThis.__aiCommandTerminalLayoutV1=bridge;write();
  return {version:bridge.version,error:bridge.error};
}
async function lock(){
  fs.mkdirSync(ROOT,{recursive:true,mode:0o700});
  const dir=path.join(ROOT,'install.lock');
  try{fs.mkdirSync(dir,{mode:0o700});}
  catch(error){
    if(error.code!=='EEXIST')throw error;
    let owner;try{owner=JSON.parse(fs.readFileSync(path.join(dir,'owner.json'),'utf8'));}catch{throw Error('Layout installer busy');}
    let live;try{live=proc(owner.pid).start===owner.start;}catch{live=false;}
    if(live)throw Error('Layout installer busy');
    fs.unlinkSync(path.join(dir,'owner.json'));fs.rmdirSync(dir);fs.mkdirSync(dir,{mode:0o700});
  }
  fs.writeFileSync(path.join(dir,'owner.json'),JSON.stringify({pid:process.pid,start:proc(process.pid).start}),{mode:0o600});
  return ()=>{fs.unlinkSync(path.join(dir,'owner.json'));fs.rmdirSync(dir);};
}
async function install(host,nudge=async()=>{}){
  const text=fs.readFileSync(host.source,'utf8');
  // Exact known map assignment; do not instrument an unrecognized future build.
  const match=[...text.matchAll(/async setTerminalLayoutInfo\((\w+)\)\{this\._workspaceLayoutInfos\.set\(\1\.workspaceId,\1\)\}/g)];
  if(match.length!==1||!text.includes('get pid(){return this._pid}'))throw Error('Unsupported VS Code terminal layout implementation');
  const column=text.indexOf('this._workspaceLayoutInfos.set(',match[0].index);
  const prefix=text.slice(0,column).split('\n');
  const unlock=await lock();let ws,call,opened=false,verified=false;
  try{
    const current=proc(host.pid);
    if(current.start!==host.start)throw Error('PTY host changed');
    if(current.args.some(arg=>arg.startsWith('--inspect')))throw Error('PTY host already configured for debugging');
    await new Promise((resolve,reject)=>{const server=net.createServer();server.once('error',()=>reject(Error('Diagnostic port 9229 is occupied')));server.listen(9229,'127.0.0.1',()=>server.close(resolve));});
    process.kill(host.pid,'SIGUSR1');opened=true;
    let target;
    for(let i=0;i<50;i++){
      try{target=(await(await fetch('http://127.0.0.1:9229/json/list',{signal:AbortSignal.timeout(200)})).json())[0];if(target)break;}catch{}
      await pause(50);
    }
    if(!target)throw Error('PTY host diagnostic connection unavailable');
    ws=new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(Error('Diagnostic connection timeout')),3000);ws.onopen=()=>{clearTimeout(timeout);resolve();};ws.onerror=()=>{clearTimeout(timeout);reject(Error('Diagnostic connection failed'));};});
    let id=0;const pending=new Map();
    ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.id){pending.get(message.id)?.(message);pending.delete(message.id);}};
    call=(method,params={})=>new Promise((resolve,reject)=>{
      const n=++id,timeout=setTimeout(()=>{pending.delete(n);reject(Error('Diagnostic request timeout: '+method));},3000);
      pending.set(n,message=>{clearTimeout(timeout);message.error?reject(Error(message.error.message)):resolve(message.result);});
      ws.send(JSON.stringify({id:n,method,params}));
    });
    const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.text);return r.result?.value;};
    if(await evaluate('process.pid')!==host.pid)throw Error('Diagnostic PID mismatch');
    verified=true;
    await call('Debugger.enable');
    // Conditional breakpoint always returns false: it captures a reference
    // without pausing execution, replacing functions, or consuming revival maps.
    const breakpoint=await call('Debugger.setBreakpointByUrl',{url:'file://'+host.source,lineNumber:prefix.length-1,columnNumber:prefix.at(-1).length,condition:'(globalThis.__aiCommandLayoutCapture=this,false)'});
    if(!breakpoint.locations?.length)throw Error('Layout method was not resolved');
    await nudge();
    let captured=false;
    for(let i=0;i<80;i++){
      captured=await evaluate('!!globalThis.__aiCommandLayoutCapture?._workspaceLayoutInfos');if(captured)break;await pause(50);
    }
    if(!captured)throw Error('No terminal layout event observed');
    const result=await evaluate(`(${publish.toString()})(globalThis.__aiCommandLayoutCapture,${JSON.stringify(host.file)},${JSON.stringify(host.start)})`);
    await evaluate('delete globalThis.__aiCommandLayoutCapture');
    if(result?.error)throw Error(result.error);
    return result;
  }finally{
    if(verified&&call){
      try{await call('Debugger.disable');}catch{}
      try{await call('Runtime.evaluate',{expression:'delete globalThis.__aiCommandLayoutCapture;setTimeout(()=>process.getBuiltinModule("node:inspector").close(),100).unref();true'});}catch{}
    }
    ws?.close();
    if(opened)await pause(200);
    unlock();
  }
}
module.exports={ROOT,SLOT,proc,findHost,install,publish};
