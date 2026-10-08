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
 return {session,clients,created,killed,vscode,controller:new Controller(vscode,backend,()=>{})};
}

test('restored tabs use an absolute bundled launcher and retain their session key',()=>{
 const {controller,session}=fixture();const options=controller.newOptions(session.key);
 assert.ok(path.isAbsolute(options.shellPath),'a PATH command can resolve to an incompatible older helper');
 assert.equal(path.basename(options.shellPath),'terminales');
 assert.equal(options.shellArgs[0],'vsc-tab-'+session.key);
 assert.equal(options.env.AI_COMMAND_TERMINALES_NATIVE,'1');
 assert.equal(options.isTransient,true,'tmux owns restoration; VS Code must not revive this client independently');
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
