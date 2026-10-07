'use strict';
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {processPaths}=require('./process-paths');

// Claude Code 2.1.258 publishes its own UI state in sessions/<PID>.json.
// Read only this metadata, never peer keys, sockets, prompts, or transcripts.
function claudeStatus(record,identity){
  if(record?.pid!==identity.pid||record.procStart!==identity.start||
    record.kind!=='interactive'||record.entrypoint!=='cli'||
    typeof record.sessionId!=='string'||!record.sessionId||
    record.pidDomain!==identity.domain)return {status:'unknown'};
  const states=new Map([['busy','working'],['shell','working'],['idle','done'],['waiting','attention']]);
  const status=states.get(record.status)||'unknown';
  return {status};
}

async function readClaude(pid,{root,procRoot='/proc',machineFile='/etc/machine-id'}={}){
  try{
    if(!root){
      const env=await processPaths(pid,{procRoot});
      root=env.CLAUDE_CONFIG_DIR||path.join(env.HOME||os.homedir(),'.claude');
    }
    const base=path.join(procRoot,String(pid));
    const [stat,machine,namespace]=await Promise.all([
      fs.readFile(path.join(base,'stat'),'utf8'),
      fs.readFile(machineFile,'utf8'),
      fs.readlink(path.join(base,'ns/pid'))
    ]);
    const start=stat.slice(stat.lastIndexOf(')')+2).split(' ')[19];
    const domain='linux:'+machine.trim()+':'+namespace;
    const file=path.join(root,'sessions',pid+'.json');
    const handle=await fs.open(file,'r');
    let data;
    try{
      if((await handle.stat()).size>64*1024)return {status:'unknown'};
      data=JSON.parse(await handle.readFile('utf8'));
    }finally{await handle.close();}
    // PID reuse or an exiting process must never inherit a previous session's state.
    const current=await fs.readFile(path.join(base,'stat'),'utf8');
    if(current.slice(current.lastIndexOf(')')+2).split(' ')[19]!==start)return {status:'unknown'};
    return claudeStatus(data,{pid,start,domain});
  }catch{return {status:'unknown'};}
}
module.exports={claudeStatus,readClaude};
