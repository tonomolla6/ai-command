'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {processPaths}=require('../agentes-terminales/process-paths');
const {readClaude}=require('../agentes-terminales/claude');

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
