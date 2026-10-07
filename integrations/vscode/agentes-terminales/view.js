'use strict';
const { Monitor }=require('./monitor');
const {NativeLayout}=require('./native-layout');
const LABELS={working:'Trabajando',done:'Terminado',attention:'Necesita atención',unknown:'Estado no confirmado',off:'Sin agente activo'};
const keyOf=t=>{
  const arg=t.creationOptions?.shellArgs?.[0];
  return typeof arg==='string'&&/^vsc-tab-[a-f0-9]{32}$/.test(arg)?arg.slice(8):undefined;
};
const cwdOf=t=>{
  const cwd=t.creationOptions?.cwd;
  return typeof cwd==='string'?cwd:cwd?.fsPath||cwd?.path;
};
const workspaceRoots=vscode=>(vscode.workspace?.workspaceFolders||[])
  .map(folder=>folder.uri?.fsPath).filter(path=>typeof path==='string'&&path.length>0);
const inside=(cwd,root)=>cwd===root||cwd.startsWith(root.endsWith('/')?root:root+'/');
function belongsToWorkspace(t,vscode,metadata){
  const roots=workspaceRoots(vscode);
  if(metadata?.workspace)return roots.includes(metadata.workspace);
  const cwd=cwdOf(t);
  // A plain VS Code terminal without a cwd is left visible for compatibility.
  return !roots.length||!cwd||roots.some(root=>inside(cwd,root));
}

class AgentsView {
  constructor(vscode,monitor=new Monitor(),report=()=>{},layout) {
    this.vscode=vscode; this.monitor=monitor; this.report=report; this.rows=[]; this.demo=false;
    this.layout=layout||(vscode.env?.appRoot?new NativeLayout(vscode,report):undefined);
    this.events=new vscode.EventEmitter(); this.onDidChangeTreeData=this.events.event;
  }
  getChildren(){return this.rows;}
  getTreeItem(row){
    const item=new this.vscode.TreeItem(row.name);
    const engine={codex:'Codex',claude:'Claude'}[row.engine];
    item.id=row.key; item.description=(engine?engine+' · ':'')+LABELS[row.status];
    item.iconPath=new this.vscode.ThemeIcon('circle-filled',new this.vscode.ThemeColor('aiCommandAgents.'+row.status));
    item.tooltip=row.name+' — '+item.description+(row.split?'\nDividida: '+row.split.join(' · '):'')+(row.detail?'\n'+row.detail:'');
    if(row.split)item.label=(row.splitIndex===0?'┌ ':row.splitIndex===row.split.length-1?'└ ':'├ ')+row.name;
    item.contextValue=row.demo?'demo':'agent';
    if(!row.demo)item.command={command:'aiCommandAgents.openTerminal',title:'Abrir terminal',arguments:[row.key]};
    return item;
  }
  async refresh(){
    if(this.running||this.demo||this.disposed)return;
    this.running=true;
    try {
      const allTerminals=new Map();
      for(const t of this.vscode.window.terminals){
        const key=keyOf(t);
        if(key&&!allTerminals.has(key))allTerminals.set(key,t);
      }
      const allStates=allTerminals.size?await this.monitor.sample(new Set(allTerminals.keys())):new Map();
      const terminals=new Map([...allTerminals].filter(([key,t])=>belongsToWorkspace(t,this.vscode,allStates.get(key))));
      const states=new Map([...allStates].filter(([key])=>terminals.has(key)));
      const currentGroups=await this.layout?.sample(terminals);
      this.orderSynced=!!currentGroups;
      if(currentGroups)this.lastGroups=currentGroups;
      const groups=currentGroups||this.lastGroups?.map(g=>g.filter(k=>terminals.has(k))).filter(g=>g.length);
      const keys=groups?groups.flat():[...terminals.keys()];
      for(const key of terminals.keys())if(!keys.includes(key))keys.push(key);
      const rows=keys.map(key=>({key,name:terminals.get(key).name,...(states.get(key)||{status:'unknown'})}));
      if(groups)for(const group of groups.filter(g=>g.length>1)){
        for(const [i,key] of group.entries())Object.assign(rows.find(r=>r.key===key),{split:group.map(k=>terminals.get(k).name),splitIndex:i});
      }
      if(this.disposed||this.demo)return;
      // Only redraw for visible changes; metadata timestamps must not flicker the list.
      const signature=JSON.stringify(rows.map(r=>[r.key,r.name,r.status,r.detail,r.split,r.engine]));
      if(signature!==this.signature){this.rows=rows;this.signature=signature;this.events.fire();}
      this.error=undefined;
    }catch(error){
      if(error.message!==this.error){this.report(error.message);this.error=error.message;}
      this.rows=this.rows.map(row=>({...row,status:'unknown',detail:'No se pudo consultar el estado'}));this.events.fire();
    }finally{this.running=false;}
  }
  async open(key){
    // Resolve against current terminals, not cached process IDs or the active tab.
    const terminal=this.vscode.window.terminals.find(t=>keyOf(t)===key&&
      (!this.rows.length||this.rows.some(row=>row.key===key&&belongsToWorkspace(t,this.vscode,row))));
    if(!terminal)return;
    const request=this.openRequest=Symbol();
    terminal.show();
    // show() crosses the extension-host boundary. Wait for the chosen terminal
    // before running a command that otherwise acts on the previous active one.
    for(let i=0;i<100;i++){
      if(this.disposed||this.openRequest!==request||!this.vscode.window.terminals.includes(terminal))return;
      if(this.vscode.window.activeTerminal===terminal){
        await this.vscode.commands.executeCommand('workbench.action.terminal.scrollToBottom');
        return;
      }
      await new Promise(resolve=>setTimeout(resolve,20));
    }
  }
  showDemo(){
    this.demo=true;this.signature=undefined;
    this.rows=['working','done','attention'].map((status,i)=>({key:'demo-'+i,name:['Prueba · trabajando','Prueba · terminado','Prueba · atención'][i],status,demo:true}));
    this.events.fire();
  }
  async live(){this.demo=false;this.signature=undefined;await this.refresh();}
  dispose(){this.disposed=true;this.layout?.dispose();this.events.dispose();}
}
module.exports={AgentsView,keyOf,cwdOf,belongsToWorkspace,LABELS};
