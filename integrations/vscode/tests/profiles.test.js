'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {processPaths}=require('../agentes-terminales/process-paths');
const {readClaude}=require('../agentes-terminales/claude');
const {findAgents,Monitor}=require('../agentes-terminales/monitor');
const {Tmux}=require('../terminales-persistentes/tmux');
const {spawnSync,execFileSync}=require('node:child_process');

test('a real detached tmux server with no attached clients is an empty list, including legacy transports',async(t)=>{
 if(spawnSync('tmux',['-V']).status!==0){t.skip('tmux not installed');return;}
 const socket='ai-empty-clients-'+process.pid+'-'+Date.now();
 const backend=new Tmux({...process.env,AI_COMMAND_TMUX_SOCKET:socket});
 try{
  execFileSync('tmux',['-L',socket,'-f','/dev/null','new-session','-d','-s','fixture'],{env:{...process.env,TMUX:''}});
  const id=execFileSync('tmux',['-L',socket,'display-message','-p','-t','fixture','#{session_id}'],{encoding:'utf8'}).trim();
  assert.deepEqual(await backend.clients(),new Map());
  assert.equal(await backend.nativeConnected({id,socket}),false);
 }finally{
  try{execFileSync('tmux',['-L',socket,'kill-server'],{stdio:'ignore'});}catch{}
 }
});

test('a renamed Python account launcher is traversed to the native agent',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'ai-process-'));
 try{
  const python=path.join(root,'python3');await fs.writeFile(python,'\x7fELFpython');
  const codex=path.join(root,'codex');await fs.writeFile(codex,'\x7fELFnative');
  for(const [pid,exe,children] of [[10,python,'11'],[11,codex,'12']]){
   const dir=path.join(root,String(pid));await fs.mkdir(path.join(dir,'task',String(pid)),{recursive:true});
   await fs.writeFile(path.join(dir,'comm'),'codex\n');await fs.symlink(exe,path.join(dir,'exe'));
   await fs.writeFile(path.join(dir,'task',String(pid),'children'),children);
  }
  assert.deepEqual(await findAgents(10,0,root),[{pid:11,engine:'codex'}]);
  await fs.writeFile(path.join(root,'10/comm'),'ai\n');
  assert.deepEqual(await findAgents(10,1,root),[{pid:11,engine:'codex'}]);
 }finally{await fs.rm(root,{recursive:true});}
});

test('monitor and persistent terminals use the tmux chosen in PATH',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'ai-tmux-'));const previous=process.env.PATH;
 try{
  const binary=path.join(root,'tmux');
  const key='a'.repeat(32);
  await fs.writeFile(binary,'#!'+process.execPath+'\nconsole.log(process.argv.includes("list-panes")?'+
   JSON.stringify('%1\t99999999\t'+key+'\t\t/home/test')+':"selected-tmux");\n',{mode:0o755});
  process.env.PATH=root+path.delimiter+previous;process.env.AI_COMMAND_TMUX_SOCKET='fixture';
  const backend=new Tmux(process.env);assert.equal(await backend.run(['-V']),'selected-tmux');
  const rows=await new Monitor().sample(new Set([key]));assert.equal(rows.get(key).status,'off');
 }finally{process.env.PATH=previous;delete process.env.AI_COMMAND_TMUX_SOCKET;await fs.rm(root,{recursive:true});}
});

test('the graphical view distinguishes Split terminals with identical launch arguments',()=>{
 const {keyOf}=require('../agentes-terminales/view');
 const options={shellArgs:['vscode-'+'a'.repeat(32)]};
 const parent={creationOptions:options},child={creationOptions:options};
 assert.notEqual(keyOf(parent),keyOf(child));assert.equal(keyOf(parent),keyOf(parent));
});

test('process paths return only the selected account directories',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'ai-plugin-'));
 try{
  const proc=path.join(root,'44');await fs.mkdir(proc);
  await fs.writeFile(path.join(proc,'environ'),'HOME=/home/test\0CLAUDE_CONFIG_DIR=/home/test/.claude-account-5\0API_KEY=fixture-private-value\0CODEX_HOME=relative-not-valid\0');
  assert.deepEqual(await processPaths(44,{procRoot:root}),{HOME:'/home/test',CLAUDE_CONFIG_DIR:'/home/test/.claude-account-5'});
 }finally{await fs.rm(root,{recursive:true});}
});

test('Claude state follows CLAUDE_CONFIG_DIR and validates process identity',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'ai-plugin-'));
 try{
  const proc=path.join(root,'proc','44');await fs.mkdir(path.join(proc,'ns'),{recursive:true});
  const fields=Array(20).fill('0');fields[0]='S';fields[19]='4242';
  await fs.writeFile(path.join(proc,'stat'),'44 (claude) '+fields.join(' '));
  await fs.symlink('pid:[77]',path.join(proc,'ns/pid'));
  const machine=path.join(root,'machine');await fs.writeFile(machine,'fixture-machine\n');
  const profile=path.join(root,'.claude-account-2');await fs.mkdir(path.join(profile,'sessions'),{recursive:true});
  await fs.writeFile(path.join(proc,'environ'),'HOME='+root+'\0CLAUDE_CONFIG_DIR='+profile+'\0');
  const file=path.join(profile,'sessions/44.json');
  const row={pid:44,procStart:'4242',pidDomain:'linux:fixture-machine:pid:[77]',kind:'interactive',entrypoint:'cli',sessionId:'fixture-session',status:'busy'};
  await fs.writeFile(file,JSON.stringify(row));
  const options={procRoot:path.join(root,'proc'),machineFile:machine};
  assert.deepEqual(await readClaude(44,options),{status:'working'});
  row.status='waiting';await fs.writeFile(file,JSON.stringify(row));
  assert.deepEqual(await readClaude(44,options),{status:'attention'});
  row.procStart='different-process';await fs.writeFile(file,JSON.stringify(row));
  assert.deepEqual(await readClaude(44,options),{status:'unknown'});
 }finally{await fs.rm(root,{recursive:true});}
});


test('native revived tabs with no creation arguments use real PID bindings',async()=>{
 const {AgentsView}=require('../agentes-terminales/view');
 const revived={name:'Working agent',creationOptions:{name:'Working agent'},processId:Promise.resolve(42)};
 const hidden={name:'Layout helper',creationOptions:{hideFromUser:true},processId:Promise.resolve(43)};
 const vscode={window:{terminals:[revived,hidden]},workspace:{workspaceFolders:[{uri:{fsPath:'/home/test/project'}}]},EventEmitter:class{constructor(){this.event=()=>{};}fire(){}}};
 const monitor={sample:async(keys,bindings)=>new Map([...keys].filter(k=>bindings.get(k)===42).map(k=>[k,{workspace:'/home/test/project',status:'working',engine:'codex'}]))};
 const view=new AgentsView(vscode,monitor);await view.refresh();
 assert.equal(view.rows.length,1);assert.equal(view.rows[0].name,revived.name);assert.equal(view.rows[0].engine,'codex');
});
