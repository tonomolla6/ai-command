'use strict';
const fs = require('node:fs/promises');
const nodePath = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { State, rootSession, visibleStatus } = require('./state');
const {readClaude}=require('./claude');
const {processPaths}=require('./process-paths');
const execute = promisify(execFile);
const LIMIT = 2 * 1024 * 1024;

async function tmux(args) {
  const {stdout} = await execute('/usr/bin/tmux', args, {timeout:2000,maxBuffer:LIMIT,
    env:{...process.env,TMUX:'',TMUX_PANE:''}});
  return stdout.trimEnd();
}

async function findAgents(pid, depth = 0) {
  if (depth > 4) return [];
  try {
    const comm = (await fs.readFile(`/proc/${pid}/comm`,'utf8')).trim();
    if (['codex','claude'].includes(comm)) return [{pid:Number(pid),engine:comm}]; // Stop before subagents.
    if (depth && !['bash','node','MainThread','sh','env'].includes(comm) && !comm.startsWith('python')) return [];
    const children = (await fs.readFile(`/proc/${pid}/task/${pid}/children`,'utf8')).trim().split(/\s+/).filter(Boolean);
    return (await Promise.all(children.map(child => findAgents(child, depth+1)))).flat();
  } catch { return []; }
}

async function metadata(path) {
  let handle;
  try {
    handle = await fs.open(path,'r');
    const data = Buffer.alloc(128*1024);
    const {bytesRead} = await handle.read(data,0,data.length,0);
    const end = data.subarray(0,bytesRead).indexOf(10);
    if (end >= 0) return JSON.parse(data.subarray(0,end).toString());
  } catch { /* A missing, rotated or incomplete file is not session evidence. */ }
  finally { await handle?.close(); }
}

async function transcript(pid) {
  const found = new Map();
  for (const fd of await fs.readdir(`/proc/${pid}/fd`)) {
    try {
      const path = await fs.readlink(`/proc/${pid}/fd/${fd}`);
      if (!/\/rollout-[^/]+\.jsonl$/.test(path)) continue;
      const id = rootSession(await metadata(path));
      if (id) found.set(path,id);
    } catch { /* Process closed an fd, or the format is not supported. */ }
  }
  if (found.size === 1) return [...found.keys()][0];
  if (found.size > 1) return undefined;

  // A resumed CLI keeps the original rollout filename, so its timestamp does
  // not match the new process. Resolve the explicit session ID through Codex's
  // local index and verify the rollout metadata before trusting the path.
  const env=await processPaths(pid);
  const home = nodePath.resolve(env.CODEX_HOME || `${env.HOME||process.env.HOME}/.codex`);
  const args = (await fs.readFile(`/proc/${pid}/cmdline`)).toString().split('\0');
  let sqliteHome=home;
  for(let i=0;i<args.length-1;i++) {
    if(['-c','--config'].includes(args[i])&&args[i+1].startsWith('sqlite_home=')) {
      try {
        const value=JSON.parse(args[i+1].slice('sqlite_home='.length));
        if(typeof value==='string'&&nodePath.isAbsolute(value))sqliteHome=value;
      }catch{}
    }
  }
  const resume = args.indexOf('resume');
  if (resume >= 0) {
    const id = args.slice(resume+1).find(arg=>/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(arg));
    if (!id) return undefined;
    try {
      const database = nodePath.join(sqliteHome, 'state_5.sqlite');
      const {stdout} = await execute('sqlite3', ['-readonly', database,
        `select rollout_path from threads where id='${id}' limit 1;`],
      {timeout:1000,maxBuffer:4096});
      const path = stdout.trim();
      const owner=path?await fs.stat(path):undefined;
      if (path && path.includes('/sessions/') && owner?.uid===process.getuid() &&
          rootSession(await metadata(path)) === id) {
        return path;
      }
    } catch { /* Never replace an explicit resume with a different session. */ }
    return undefined;
  }

  // Recent Codex versions close the rollout between writes. Filenames have
  // whole seconds; only the ISO metadata timestamp can validate milliseconds.
  const started = (await fs.stat(`/proc/${pid}`)).ctimeMs;
  const cwd = await fs.stat(`/proc/${pid}/cwd`);
  const directories = new Set([started,started+5000].map(at=>{
    const day=new Date(at);
    return nodePath.join(home,'sessions',String(day.getFullYear()),
      String(day.getMonth()+1).padStart(2,'0'),String(day.getDate()).padStart(2,'0'));
  }));
  const candidates = [];
  for (const directory of directories) {
    let names;
    try { names=await fs.readdir(directory); } catch { continue; }
    for (const name of names) {
      const match = /^rollout-(\d{4}-\d\d-\d\d)T(\d\d)-(\d\d)-(\d\d)-.*\.jsonl$/.exec(name);
      if (!match) continue;
      const coarse = new Date(`${match[1]}T${match[2]}:${match[3]}:${match[4]}`).getTime();
      if (coarse < Math.floor(started/1000)*1000 || coarse > started+5000) continue;
      const path = nodePath.join(directory,name);
      const record = await metadata(path);
      if (!rootSession(record)) continue;
      const created = Date.parse(record.payload.timestamp);
      if (!Number.isFinite(created) || created < started || created > started+5000 ||
          typeof record.payload.cwd !== 'string') continue;
      try {
        const folder=await fs.stat(record.payload.cwd);
        // Inodes also recognize the workspace's legacy bind-mount aliases.
        if (folder.dev === cwd.dev && folder.ino === cwd.ino) candidates.push(path);
      } catch { /* An inaccessible or foreign cwd is not an association. */ }
    }
  }
  return candidates.length === 1 ? candidates[0] : undefined;
}

class Tail {
  constructor(path) { this.path=path; this.offset=0; this.partial=''; this.state=new State(); }
  async update() {
    const handle=await fs.open(this.path,'r');
    try {
      const stat=await handle.stat();
      if (stat.size < this.offset || this.inode !== undefined && this.inode !== stat.ino) {
        this.offset=0; this.partial=''; this.state=new State();
      }
      this.inode=stat.ino;
      const limit=this.offset===0?4*LIMIT:LIMIT;
      if (stat.size-this.offset>limit) {
        this.offset=stat.size-limit; this.partial=''; this.state=new State(); this.skip=true;
      }
      const data=Buffer.alloc(Math.min(limit,stat.size-this.offset));
      const {bytesRead}=await handle.read(data,0,data.length,this.offset);
      this.offset+=bytesRead;
      // Keeping bytes until a complete line also handles split UTF-8 characters.
      const all=Buffer.concat([Buffer.from(this.partial,'base64'),data.subarray(0,bytesRead)]);
      let start=0;
      for(let end=all.indexOf(10);end>=0;end=all.indexOf(10,start)) {
        if(this.skip) this.skip=false;
        else { try { this.state.accept(JSON.parse(all.subarray(start,end).toString())); } catch {} }
        start=end+1;
      }
      if(all.length-start>LIMIT) {this.partial='';this.skip=true;}
      else this.partial=all.subarray(start).toString('base64');
    } finally { await handle.close(); }
    return this.state;
  }
}

class Monitor {
  constructor({claudeOptions}={}) { this.cache=new Map(); this.tails=new Map(); this.claudeOptions=claudeOptions; }
  async sample(keys) {
    const format='#{pane_id}\t#{pane_pid}\t#{@ai_command_vscode_key}\t#{@ai_command_agent_mark}\t#{@ai_command_vscode_workspace}';
    const panes=(await tmux(['list-panes','-a','-F',format])).split('\n').map(line=>{
      const [pane,pid,key,raw,workspace]=line.split('\t');
      let mark; try { mark=JSON.parse(raw); } catch {}
      return {pane,pid:Number(pid),key,mark,workspace};
    }).filter(p=>keys.has(p.key));
    const result=new Map();
    const used=new Set();
    for(const pane of panes) {
      if(result.has(pane.key)) { result.set(pane.key,{status:'unknown',detail:'Varios paneles tmux'}); continue; }
      const now=Date.now();
      let agent=this.cache.get(pane.pane);
      if(!agent || now-agent.checked>5000 || agent.panePid!==pane.pid) {
        const agents=await findAgents(pane.pid);
        const {pid,engine}=agents.length===1?agents[0]:{};
        let path; if(pid&&engine==='codex') { try { path=await transcript(pid); } catch {} }
        agent={pid,engine,path,checked:now,panePid:pane.pid,ambiguous:agents.length>1};
        this.cache.set(pane.pane,agent);
      }
      let row={...pane,status:agent.pid||agent.ambiguous?'unknown':'off',agentPid:agent.pid,engine:agent.engine};
      if(agent.engine==='claude')row={...row,...await readClaude(agent.pid,this.claudeOptions)};
      if(agent.path) {
        used.add(agent.path);
        let tail=this.tails.get(agent.path);
        if(!tail) {tail=new Tail(agent.path); this.tails.set(agent.path,tail);}
        try {
          const state=await tail.update();
          row={...row,...state.result(pane.mark,agent.pid)};
        } catch {row.status='unknown';}
      }
      if(agent.engine==='codex') {
        // Current activity and blocking approval controls are direct evidence,
        // even if a resumed session has no observable rollout descriptor.
        try {
          const screen=await tmux(['capture-pane','-p','-t',pane.pane]);
          row.status=visibleStatus(row.status,screen);
        } catch { /* Preserve a status confirmed by the transcript. */ }
        if(!agent.path)row.detail='No se ha podido asociar el registro de esta sesión de Codex';
      }
      result.set(pane.key,row);
    }
    for(const path of this.tails.keys()) if(!used.has(path)) this.tails.delete(path);
    for(const pane of this.cache.keys()) if(!panes.some(p=>p.pane===pane)) this.cache.delete(pane);
    return result;
  }
}

const findCodex=async pid=>(await findAgents(pid)).filter(a=>a.engine==='codex').map(a=>a.pid);
module.exports={Monitor,Tail,findAgents,findCodex,transcript};
