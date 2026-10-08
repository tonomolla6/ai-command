'use strict';
// Real VS Code windows, private HOME and a uniquely owned tmux socket. No AI
// credentials, model calls or writes to an existing user's terminal.
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {runTests}=require('@vscode/test-electron');

async function main(){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'ai-vscode-lifecycle-'));
 const socket='ai-test-'+process.pid+'-'+Date.now();
 const integration=path.resolve(__dirname,'../..');
 const extensions=path.join(root,'extensions');
 const home=path.join(root,'home'),workspace=path.join(root,'project');
 fs.mkdirSync(home);fs.mkdirSync(workspace);fs.mkdirSync(extensions);
 const development=[];
 for(const name of ['terminales-persistentes','agentes-terminales']){
  const dest=path.join(extensions,'ai-command.'+name);
  fs.cpSync(path.join(integration,name),dest,{recursive:true,filter:p=>!p.split(path.sep).includes('node_modules')});
  development.push(dest);
 }
 fs.cpSync(path.join(integration,'bin'),path.join(development[0],'bin'),{recursive:true});
 fs.writeFileSync(path.join(home,'.tmux.conf'),'set -g history-limit 50000\nset -g mouse off\nset -g window-size latest\nset -g status off\n');
 const env={...process.env,HOME:home,TMUX:'',TMUX_PANE:'',AI_COMMAND_TMUX_SOCKET:socket,
  AI_COMMAND_WORKSPACE_ROOT:workspace,AI_VSCODE_FIXTURE_ROOT:root};
 for(const key of Object.keys(env))if(/^(CODEX_|CLAUDE_|ANTHROPIC_|OPENAI_)/.test(key))delete env[key];
 try{
  for(const phase of ['initial','restart','restart-again']){
   await runTests({version:process.env.VSCODE_TEST_VERSION||'stable',
    extensionDevelopmentPath:development,extensionTestsPath:path.join(__dirname,'lifecycle.js'),
    extensionTestsEnv:{...env,AI_VSCODE_FIXTURE_PHASE:phase},
    launchArgs:[workspace,'--user-data-dir='+path.join(root,'data'),'--extensions-dir='+extensions,
     '--skip-welcome','--skip-release-notes','--disable-workspace-trust','--no-sandbox','--disable-gpu']});
  }
  console.log('PASS: native Split, PID bindings, two real VS Code restarts, same tmux processes, no duplicate tabs.');
 }finally{
  try{execFileSync('tmux',['-L',socket,'kill-server'],{env,stdio:'ignore'});}catch{}
  fs.rmSync(root,{recursive:true,force:true});
 }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
