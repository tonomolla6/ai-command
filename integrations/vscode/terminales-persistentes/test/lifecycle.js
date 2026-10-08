'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vscode=require('vscode');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){for(let i=0;i<160;i++){const value=await check();if(value)return value;await pause(100);}throw Error('Timed out: '+label);}

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
  await controller.sync();await controller.flush();
  assert.equal(agents.provider.rows.length,4,'graphical monitor must show cloned-argument Split too');
  const rows=await sessions();
  fs.writeFileSync(baseline,JSON.stringify({rows:rows.map(s=>({ref:s.ref,key:s.key,created:s.created,group:s.group})),pids:originalPids}));
  // Use only this fixture's shell. A long inline conversation exercises the
  // control renderer and tmux scrollback without calling any AI service.
  first.sendText("python3 -c 'print(\"\\n\".join(\"SCROLL-FIXTURE-%04d\"%n for n in range(500)))'");
  await until(async()=>Number(await controller.backend.run(['display-message','-p','-t',selected.id+':','#{history_size}']))>400,'inline scrollback retained');
  console.log('PASS initial: selected native Split, unique sessions, all agent rows, split layout, 500-line scrollback');
 }else{
  const expected=JSON.parse(fs.readFileSync(baseline));
  await until(async()=>{await controller.sync();return controller.tracked.size===expected.rows.length;},'restored tabs');
  assert.equal(vscode.window.terminals.filter(t=>!t.creationOptions?.hideFromUser).length,expected.rows.length,'restart must not manufacture extra blank tabs');
  const actual=await sessions();
  assert.deepEqual(actual.map(s=>({ref:s.ref,key:s.key,created:s.created,group:s.group})),expected.rows,'session identities/layout must survive restart');
  const pids=(await controller.backend.run(['list-panes','-a','-F','#{pane_pid}'])).split('\n');
  assert.ok(expected.pids.every(pid=>pids.includes(pid)),'original shell processes must remain alive');
  await until(async()=>{await agents.refresh();return agents.provider.orderSynced;},'restored graphical groups');
  assert.ok(agents.provider.lastGroups.some(g=>g.length===2),'restored Split group must still contain two terminals');
  assert.equal(agents.provider.rows.length,expected.rows.length);
  await controller.flush();
  console.log('PASS '+process.env.AI_VSCODE_FIXTURE_PHASE+': same processes and groups; zero duplicated terminals');
 }
};
