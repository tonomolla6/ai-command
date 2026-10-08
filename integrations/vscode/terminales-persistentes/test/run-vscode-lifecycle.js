'use strict';
// Three ordinary VS Code windows: the --extensionTestsPath harness skips normal
// terminal shutdown persistence, so an installed fixture driver is used instead.
// Everything runs inside a private HOME and an owned tmux socket, without accounts.
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {execFileSync,spawn}=require('node:child_process');
const {downloadAndUnzipVSCode}=require('@vscode/test-electron');
async function launch(executable,args,env){
 await new Promise((resolve,reject)=>{
  const child=spawn(executable,args,{env,stdio:['ignore','ignore','pipe']});
  let error='';child.stderr.on('data',data=>{error=(error+data.toString()).slice(-2000);});
  const timer=setTimeout(()=>{child.kill('SIGTERM');reject(Error('VS Code lifecycle timeout'));},90000);
  child.once('error',e=>{clearTimeout(timer);reject(e);});
  child.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(Error('VS Code exit '+code+': '+error));});
 });
}
async function main(){
 const executable=await downloadAndUnzipVSCode(process.env.VSCODE_TEST_VERSION||'stable');
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'ai-vscode-lifecycle-'));
 const socket='ai-test-'+process.pid+'-'+Date.now();
 const integration=path.resolve(__dirname,'../..');
 const extensions=path.join(root,'extensions');
 const home=path.join(root,'home'),workspace=path.join(root,'project');
 fs.mkdirSync(home);fs.mkdirSync(workspace);fs.mkdirSync(extensions);
 for(const name of ['terminales-persistentes','agentes-terminales']){
  const pkg=JSON.parse(fs.readFileSync(path.join(integration,name,'package.json')));
  const dest=path.join(extensions,pkg.publisher+'.'+name+'-'+pkg.version);
  fs.cpSync(path.join(integration,name),dest,{recursive:true,filter:p=>!p.split(path.sep).some(v=>['node_modules','.vscode-test'].includes(v))});
  if(name==='terminales-persistentes')fs.cpSync(path.join(integration,'bin'),path.join(dest,'bin'),{recursive:true});
 }
 const driver=path.join(extensions,'ai-command.lifecycle-driver-0.0.1');fs.mkdirSync(driver);
 fs.copyFileSync(path.join(__dirname,'lifecycle.js'),path.join(driver,'lifecycle.js'));
 fs.writeFileSync(path.join(driver,'package.json'),JSON.stringify({name:'lifecycle-driver',publisher:'ai-command',version:'0.0.1',engines:{vscode:'^1.85.0'},main:'./extension.js',activationEvents:['onStartupFinished']}));
 fs.writeFileSync(path.join(driver,'extension.js'),`
 const fs=require('node:fs'),path=require('node:path'),vscode=require('vscode');
 exports.activate=async()=>{
  const root=process.env.AI_VSCODE_FIXTURE_ROOT;
  if(!root||!path.basename(root).startsWith('ai-vscode-lifecycle-'))throw Error('Owned fixture required');
  const messages=[],log=console.log;console.log=(...args)=>{messages.push(args.map(x=>typeof x==='string'?x:JSON.stringify(x)).join(' '));log(...args);};
  let result;
  try{const checks=await require('./lifecycle').run();result={ok:true,messages,checks};}
  catch(error){result={ok:false,messages,error:error.stack};}
  finally{console.log=log;}
  fs.writeFileSync(path.join(root,process.env.AI_VSCODE_FIXTURE_PHASE+'.json'),JSON.stringify(result));
  await vscode.commands.executeCommand('workbench.action.quit');
 };
 `);
 fs.writeFileSync(path.join(home,'.tmux.conf'),'set -g history-limit 50000\nset -g mouse off\nset -g window-size latest\nset -g status off\n');
 const env={...process.env,HOME:home,TMUX:'',TMUX_PANE:'',AI_COMMAND_TMUX_SOCKET:socket,
  AI_COMMAND_WORKSPACE_ROOT:workspace,AI_VSCODE_FIXTURE_ROOT:root};
 for(const key of Object.keys(env))if(/^(CODEX_|CLAUDE_|ANTHROPIC_|OPENAI_)/.test(key))delete env[key];
 try{
  for(const phase of ['initial','restart','restart-again']){
   await launch(executable,[workspace,'--new-window','--user-data-dir='+path.join(root,'data'),'--extensions-dir='+extensions,
    '--skip-welcome','--skip-release-notes','--disable-workspace-trust','--no-sandbox','--disable-gpu','--remote-debugging-port=0'],{...env,AI_VSCODE_FIXTURE_PHASE:phase});
   const result=JSON.parse(fs.readFileSync(path.join(root,phase+'.json')));
   for(const line of result.messages)console.log(line);
   if(result.checks)console.log('PASS '+phase+': '+JSON.stringify(result.checks));
   if(!result.ok)throw Error(result.error);
  }
  console.log('PASS: native Split/Join, pane sizes, mouse wheel, two normal VS Code restarts, same processes and no duplicate tabs.');
 }finally{
  try{execFileSync('tmux',['-L',socket,'kill-server'],{env,stdio:'ignore'});}catch{}
  fs.rmSync(root,{recursive:true,force:true});
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
