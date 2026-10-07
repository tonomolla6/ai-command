'use strict';
const fs=require('node:fs');
const {findHost,install}=require('./layout-bridge');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function orderFromSnapshots(terminals,snapshots){
  const byPid=new Map();
  for(const [key,pid] of terminals){if(!Number.isInteger(pid)||byPid.has(pid))return;byPid.set(pid,key);}
  const groups=[],seen=new Set();
  for(const snapshot of snapshots){
    for(const layout of snapshot.layouts||[]){
      for(const tab of layout.tabs||[]){
        const keys=[];
        for(const pid of tab.pids||[]){const key=byPid.get(pid);if(!key)continue;if(seen.has(key))return;seen.add(key);keys.push(key);}
        if(!keys.length)continue;
        groups.push(keys);
      }
    }
  }
  // Never declare a partial or stale layout synchronized. New/reviving tabs
  // may take a moment to publish their process IDs and grouping.
  return seen.size===terminals.size&&seen.size>0?groups:undefined;
}
class NativeLayout {
  constructor(vscode,report=()=>{}){this.vscode=vscode;this.report=report;this.pids=new WeakMap();this.hosts=new Map();}
  async sample(terminals){
    if(this.disposed)return;
    const pids=new Map();
    await Promise.all([...terminals].map(async([key,t])=>{
      let pid=this.pids.get(t);
      if(!pid){pid=await Promise.race([t.processId,pause(100)]);if(pid)this.pids.set(t,pid);}
      pids.set(key,pid);
    }));
    const relevant=new Map();
    for(const pid of pids.values()){
      if(!pid)continue;
      try{const h=findHost(pid,this.vscode.env.appRoot);if(h)relevant.set(h.file,h);}catch{}
    }
    const snapshots=[];
    for(const [file,host] of relevant){
      let entry=this.hosts.get(file);if(!entry){entry={host};this.hosts.set(file,entry);}
      let snapshot;
      try{const data=JSON.parse(fs.readFileSync(file,'utf8'));if(data.version===1&&data.pid===host.pid&&data.start===host.start&&Date.now()-data.at<3000)snapshot=data;}catch{}
      if(snapshot)snapshots.push(snapshot);
      else if(!entry.pending&&Date.now()>=(entry.retryAt||0)){
        let temporary;
        entry.pending=install(host,async()=>{
          if(this.disposed)throw Error('Layout view disposed');
          // A private, transient PTY makes VS Code publish its layout. It never
          // creates a tmux session, becomes visible, or sends input to a user.
          const closed=new this.vscode.EventEmitter(),output=new this.vscode.EventEmitter();
          temporary=this.vscode.window.createTerminal({name:'AI Command layout initialization',hideFromUser:true,isTransient:true,pty:{onDidWrite:output.event,onDidClose:closed.event,open:()=>setTimeout(()=>closed.fire(0),150),close:()=>{closed.dispose();output.dispose();}}});
        }).catch(error=>{entry.error=error.message;entry.retryAt=Date.now()+30000;this.report('Orden de terminales: '+error.message);}).finally(()=>{temporary?.dispose();entry.pending=undefined;});
      }
    }
    return orderFromSnapshots(pids,snapshots);
  }
  dispose(){this.disposed=true;}
}
module.exports={NativeLayout,orderFromSnapshots};
