'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vscode=require('vscode');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){for(let i=0;i<160;i++){const value=await check();if(value)return value;await pause(100);}throw Error('Timed out: '+label);}

async function rendererSession(root){
 const port=Number(fs.readFileSync(path.join(root,'data/DevToolsActivePort'),'utf8').split('\n')[0]);
 const targets=await (await fetch('http://127.0.0.1:'+port+'/json/list')).json();
 const page=targets.find(t=>t.type==='page'&&t.url.includes('workbench'));
 assert.ok(page,'isolated VS Code renderer must expose the test-only debugger');
 const ws=new WebSocket(page.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
 let next=0;const pending=new Map();
 ws.onmessage=e=>{const message=JSON.parse(e.data);if(message.id){pending.get(message.id)?.(message);pending.delete(message.id);}};
 const call=(method,params={})=>new Promise((resolve,reject)=>{
  const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Renderer request timeout: '+method));},5000);
  pending.set(id,message=>{clearTimeout(timer);message.error?reject(Error(message.error.message)):resolve(message.result);});ws.send(JSON.stringify({id,method,params}));
 });
 return {call,close:()=>ws.close()};
}

async function checkGraphicalJoin(root,agents,second,last){
 await vscode.workspace.getConfiguration('terminal.integrated').update('tabs.enabled',true,vscode.ConfigurationTarget.Global);
 last.show();await until(()=>vscode.window.activeTerminal===last,'join source tab');
 const renderer=await rendererSession(root);
 try{
  const expression=`[...document.querySelectorAll('.terminal-tabs-entry')].map(e=>{const r=e.getBoundingClientRect();return {name:e.textContent,x:r.x+r.width/2,y:r.y+r.height/2,height:r.height};}).filter(e=>e.height>0)`;
  const tab=await until(async()=> (await renderer.call('Runtime.evaluate',{expression,returnByValue:true})).result.value.find(t=>t.name.includes('Second')),'native tab to join');
  await renderer.call('Input.dispatchMouseEvent',{type:'mousePressed',x:tab.x,y:tab.y,button:'left',buttons:1,modifiers:2,clickCount:1});
  await renderer.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:tab.x,y:tab.y,button:'left',buttons:0,modifiers:2,clickCount:1});
  await vscode.commands.executeCommand('workbench.action.terminal.joinActiveTab');
  await until(async()=>{await agents.refresh();return agents.provider.lastGroups?.some(group=>group.length===2&&group.some(k=>agents.provider.terminals.get(k)===second)&&group.some(k=>agents.provider.terminals.get(k)===last));},'native joined group');
  second.show();await until(()=>vscode.window.activeTerminal===second,'resize joined pane');
  await vscode.commands.executeCommand('workbench.action.terminal.resizePaneRight');
  await vscode.commands.executeCommand('workbench.action.terminal.resizePaneRight');
  console.log('PASS: native Join combines the selected tabs and preserves a resized group');
 }finally{renderer.close();}
}

async function graphicalGroups(controller,agents){
 const byPid=new Map();
 for(const [terminal,record] of controller.tracked)byPid.set(await terminal.processId,record.session.ref);
 const result=[];
 for(const entry of agents.provider.layout.hosts.values()){
  const snapshot=JSON.parse(fs.readFileSync(entry.host.file));
  for(const layout of snapshot.layouts)for(const tab of layout.tabs){
   if(tab.pids.some(pid=>byPid.has(pid)))result.push(tab.pids.map((pid,i)=>({ref:byPid.get(pid),size:tab.sizes[i]})));
  }
 }
 return result;
}

async function checkGraphicalScroll(root){
 const {call,close}=await rendererSession(root);
 const expression=`[...document.querySelectorAll('.terminal.xterm')].map((e,index)=>{const r=e.getBoundingClientRect();const lines=[...(e.querySelector('.xterm-rows')?.textContent||'').matchAll(/SCROLL-FIXTURE-(\\d{4})/g)].map(m=>Number(m[1]));return {index,x:r.x+r.width/2,y:r.y+r.height/2,height:r.height,first:lines.length?Math.min(...lines):null};}).filter(e=>e.height>0&&e.first!==null)`;
 try{
  const read=async()=> (await call('Runtime.evaluate',{expression,returnByValue:true})).result.value;
  let viewport;
  try{viewport=await until(async()=> (await read())[0],'native graphical scrollback');}
  catch(error){console.log('Fixture scroll nodes',(await call('Runtime.evaluate',{expression:`[...document.querySelectorAll('[class*="xterm"]')].slice(0,25).map(e=>({class:e.className,top:e.scrollTop,height:e.clientHeight,scroll:e.scrollHeight}))`,returnByValue:true})).result.value);throw error;}
  await call('Input.dispatchMouseEvent',{type:'mouseWheel',x:viewport.x,y:viewport.y,deltaX:0,deltaY:-600});
  await until(async()=> (await read()).some(e=>e.index===viewport.index&&e.first<viewport.first),'normal mouse wheel scroll in VS Code');
  console.log('PASS: native scrollbar and mouse wheel move through the inline conversation');
 }finally{close();}
}

exports.run=async()=>{
 const root=process.env.AI_VSCODE_FIXTURE_ROOT;
 if(!root||!path.basename(root).startsWith('ai-vscode-lifecycle-'))throw Error('Private lifecycle fixture required');
 const terminalExtension=vscode.extensions.getExtension('ai-command.terminales-persistentes');
 const agentsExtension=vscode.extensions.getExtension('ai-command.agentes-terminales');
 const api=await terminalExtension.activate();const agents=await agentsExtension.activate();
 const controller=api.controller;
 await vscode.workspace.getConfiguration('terminal.integrated').update('defaultProfile.linux','AI Command tmux persistente',vscode.ConfigurationTarget.Global);
 await vscode.workspace.getConfiguration('terminal.integrated').update('enablePersistentSessions',true,vscode.ConfigurationTarget.Global);
 assert.ok(controller,'Controller API must refer to the real activated instance');
 const sessions=()=>controller.backend.sessions();
 const baseline=path.join(root,'baseline.json');
 if(process.env.AI_VSCODE_FIXTURE_PHASE==='initial'){
  const first=vscode.window.createTerminal({...controller.newOptions(),name:'First'});
  const second=vscode.window.createTerminal({...controller.newOptions(),name:'Second'});
  const last=vscode.window.createTerminal({...controller.newOptions(),name:'Last'});
  await until(async()=>{await controller.sync();return controller.tracked.size===3;},'three independent real terminals');
  const selected=controller.tracked.get(first).session;
  const originalPids=(await controller.backend.run(['list-panes','-a','-F','#{pane_pid}'])).split('\n');
  first.show();await until(()=>vscode.window.activeTerminal===first,'selected Split parent');
  const before=new Set(vscode.window.terminals);
  await vscode.commands.executeCommand('workbench.action.terminal.split');
  const child=await until(()=>vscode.window.terminals.find(t=>!before.has(t)&&!t.creationOptions?.hideFromUser),'native Split child');
  await until(async()=>{await controller.sync();return controller.tracked.has(child);},'Split client bound to its own tmux session');
  assert.notEqual(controller.tracked.get(child).session.key,selected.key,'Split must never attach to its parent session');
  assert.equal((await sessions()).length,4,'exactly one new tmux session for Split');
  console.log('Fixture Split arguments',JSON.stringify({first:first.creationOptions.shellArgs,child:child.creationOptions.shellArgs,
   locationType:typeof child.creationOptions.location,location:typeof child.creationOptions.location==='number'?child.creationOptions.location:Object.keys(child.creationOptions.location||{})}));
  // Exercise the native layout bridge against real VS Code, including groups.
  try{await until(async()=>{await agents.refresh();return agents.provider.orderSynced;},'complete native terminal layout');}
  catch(error){console.log('Fixture layout diagnostics',JSON.stringify({appRoot:vscode.env.appRoot,hosts:[...agents.provider.layout.hosts.values()].map(e=>({host:e.host,error:e.error,pending:!!e.pending}))}));throw error;}
  const group=agents.provider.lastGroups.find(g=>g.includes([...agents.provider.terminals].find(([,t])=>t===first)[0]));
  assert.equal(group.length,2,'native Split must use the selected group, not Last');
  assert.ok(group.some(k=>agents.provider.terminals.get(k)===child));
  assert.ok(!group.some(k=>agents.provider.terminals.get(k)===last));
  await checkGraphicalJoin(root,agents,second,last);
  await pause(1000);await agents.refresh();
  await controller.sync();await controller.flush();
  assert.equal(agents.provider.rows.length,4,'graphical monitor must show cloned-argument Split too');
  const rows=await sessions();
  fs.writeFileSync(baseline,JSON.stringify({rows:rows.map(s=>({ref:s.ref,key:s.key,created:s.created,group:s.group})),pids:originalPids,graphicalGroups:await graphicalGroups(controller,agents)}));
  // Use only this fixture's shell. A long inline conversation exercises the
  // control renderer and tmux scrollback without calling any AI service.
  first.show();await until(()=>vscode.window.activeTerminal===first,'scroll test selected terminal');
  first.sendText("python3 -c 'print(\"\\n\".join(\"SCROLL-FIXTURE-%04d\"%n for n in range(500)))'");
  await until(async()=>Number(await controller.backend.run(['display-message','-p','-t',selected.id+':','#{history_size}']))>400,'inline scrollback retained');
  await checkGraphicalScroll(root);
  console.log('PASS initial: selected native Split, unique sessions, all agent rows, split layout, 500-line scrollback');
 }else{
  const expected=JSON.parse(fs.readFileSync(baseline));
  assert.equal(controller.lastRestored,0,'normal restarts must revive the native graphical layout before fallback');
  await until(async()=>{await controller.sync();return controller.tracked.size===expected.rows.length;},'restored tabs');
  assert.equal(vscode.window.terminals.filter(t=>!t.creationOptions?.hideFromUser).length,expected.rows.length,'restart must not manufacture extra blank tabs');
  const actual=await sessions();
  assert.deepEqual(actual.map(s=>({ref:s.ref,key:s.key,created:s.created,group:s.group})),expected.rows,'session identities/layout must survive restart');
  const pids=(await controller.backend.run(['list-panes','-a','-F','#{pane_pid}'])).split('\n');
  assert.ok(expected.pids.every(pid=>pids.includes(pid)),'original shell processes must remain alive');
  await until(async()=>{await agents.refresh();return agents.provider.orderSynced;},'restored graphical groups');
  assert.ok(agents.provider.lastGroups.some(g=>g.length===2),'restored Split group must still contain two terminals');
  assert.equal(agents.provider.rows.length,expected.rows.length);
  const groups=await graphicalGroups(controller,agents);
  assert.deepEqual(groups.map(g=>g.map(r=>r.ref)),expected.graphicalGroups.map(g=>g.map(r=>r.ref)),'Join/Split grouping and order must survive');
  for(const [i,group] of groups.entries())for(const [j,row] of group.entries())assert.ok(Math.abs(row.size-expected.graphicalGroups[i][j].size)<.025,'resized joined/split panes must retain their relative sizes');
  await controller.flush();
  console.log('PASS '+process.env.AI_VSCODE_FIXTURE_PHASE+': same processes and groups; zero duplicated terminals');
 }
 return {phase:process.env.AI_VSCODE_FIXTURE_PHASE,nativeTabs:vscode.window.terminals.filter(t=>!t.creationOptions?.hideFromUser).length,restoredByFallback:controller.lastRestored};
};
