'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {Controller}=require('../terminales-persistentes/controller');

function fixture(){
 const session={id:'$7',created:'1234',name:'terminal-1',workspace:'/home/test/project',
  key:'a'.repeat(32),restore:true,label:'Working tab',order:0};
 const clients=new Map(),created=[],killed=[];
 const vscode={workspace:{workspaceFolders:[{uri:{fsPath:session.workspace}}]},
  TerminalExitReason:{User:1,Process:2},window:{terminals:[],createTerminal(options){
   const terminal={creationOptions:options,name:options.name,processId:Promise.resolve(99),
    show(){vscode.window.activeTerminal=this;},dispose(){this.disposed=true;}};
   created.push(terminal);vscode.window.terminals.push(terminal);return terminal;
  }}};
 const backend={sessions:async()=>[session],clients:async()=>clients,
  persistentKey:async s=>s.key,set:async()=>{},kill:async s=>killed.push(s)};
 backend.ticket=async()=> 'vscode-'+'b'.repeat(32);
 return {session,clients,created,killed,vscode,controller:new Controller(vscode,backend,()=>{})};
}

test('restored tabs use an absolute bundled launcher and retain their session key',()=>{
 const {controller,session}=fixture();const options=controller.newOptions(session.key);
 assert.ok(path.isAbsolute(options.shellPath),'a PATH command can resolve to an incompatible older helper');
 assert.equal(path.basename(options.shellPath),'terminales');
 assert.equal(options.shellArgs[0],'vsc-tab-'+session.key);
 assert.equal(options.env.AI_COMMAND_TERMINALES_NATIVE,'1');
 assert.equal(options.isTransient,false,'VS Code must retain graphical tab/split layout while tmux owns the processes');
});

test('recovery explicitly requires an existing session instead of creating a blank one',async()=>{
 const {controller,session,created}=fixture();
 await controller.restore();
 assert.equal(created[0].creationOptions.shellArgs[0],'vsc-resume-'+session.key);
});

test('startup restores clients without relaunching live terminals',async()=>{
 const {controller}=fixture();let migrations=0;
 controller.migrate=async()=>{migrations++;};
 await controller.start();assert.equal(migrations,0);
});

test('VS Code revived clients are adopted without creating additional tabs',async()=>{
 const {controller,session,clients,created,vscode,killed}=fixture();
 const terminal={name:session.label,creationOptions:{cwd:session.workspace},processId:Promise.resolve(42)};
 vscode.window.terminals.push(terminal);clients.set(42,session.id);
 assert.equal(await controller.restore(),0);
 assert.equal(await controller.restore(),0);
 assert.equal(created.length,0);assert.equal(killed.length,0);
});

test('late revival closes only the recovery client and never the shared session',async()=>{
 const {controller,session,clients,created,vscode,killed}=fixture();
 assert.equal(await controller.restore(),1);const recovery=created[0];
 const original={name:session.label,creationOptions:{cwd:session.workspace},processId:Promise.resolve(42),
  show(){vscode.window.activeTerminal=this;}};
 vscode.window.terminals.push(original);clients.set(42,session.id);
 await controller.sync();
 assert.equal(recovery.disposed,true);assert.equal(original.disposed,undefined);
 recovery.exitStatus={reason:vscode.TerminalExitReason.User};await controller.close(recovery);
 assert.equal(killed.length,0);assert.equal(await controller.restore(),0);
});

test('background adoption preserves the selected terminal used by native Split',async()=>{
 const {controller,session,clients,vscode}=fixture();let focusChanges=0;
 const selected={name:'Selected',processId:Promise.resolve(1)};
 const other={name:'Shell',processId:Promise.resolve(42),show(){focusChanges++;vscode.window.activeTerminal=this;}};
 vscode.window.terminals.push(selected,other);vscode.window.activeTerminal=selected;clients.set(42,session.id);
 await controller.sync();assert.equal(vscode.window.activeTerminal,selected);assert.equal(focusChanges,0);
});

test('a Split cloned key is bound to its own client and cannot close the parent',async()=>{
 const {controller,session,clients,vscode,killed}=fixture();
 const split={...session,id:'$8',key:'c'.repeat(32),created:'1235'};
 controller.backend.sessions=async()=>[session,split];
 const parent={name:'Parent',creationOptions:controller.newOptions(session.key),processId:Promise.resolve(42)};
 const child={name:'Child',creationOptions:controller.newOptions(session.key),processId:Promise.resolve(43)};
 vscode.window.terminals.push(parent,child);clients.set(42,session.id);clients.set(43,split.id);
 await controller.sync();assert.equal(controller.tracked.get(child).session.id,split.id);
 child.exitStatus={reason:vscode.TerminalExitReason.User};await controller.close(child);
 assert.deepEqual(killed,[split]);assert.equal(controller.tracked.get(parent).session.id,session.id);
});
