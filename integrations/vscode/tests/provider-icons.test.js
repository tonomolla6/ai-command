'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {ProviderIcons}=require('../agentes-terminales/provider-icons');
const {AgentsView}=require('../agentes-terminales/view');
const {findAgents}=require('../agentes-terminales/monitor');

function vscode(overrides={}){
 return {Uri:{file:filename=>({fsPath:filename})},window:{activeColorTheme:{kind:2}},
  workspace:{getConfiguration:()=>({get:key=>key==='colorCustomizations'?overrides:undefined})},
  TreeItem:class{constructor(label){this.label=label;}},ThemeIcon:class{},ThemeColor:class{},
  EventEmitter:class{constructor(){this.event=()=>{};}fire(){}dispose(){}}};
}
const dots=svg=>[...svg.matchAll(/<circle[^>]*fill="([^"]+)"/g)].map(match=>match[1]);

test('activity and provider dots are independent, with a readable light and dark variant',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'ai-provider-icons-'));
 try{
  const icons=new ProviderIcons(vscode(),root);
  const working=icons.icon({engine:'codex',status:'working'});
  const done=icons.icon({engine:'codex',status:'done'});
  const claude=icons.icon({engine:'claude',status:'done'});
  const first=dots(await fs.readFile(working.dark.fsPath,'utf8'));
  const next=dots(await fs.readFile(done.dark.fsPath,'utf8'));
  const other=dots(await fs.readFile(claude.dark.fsPath,'utf8'));
  assert.equal(first.length,2);assert.notEqual(first[0],next[0]);assert.equal(first[1],next[1]);
  assert.equal(next[0],other[0]);assert.notEqual(next[1],other[1]);
  assert.notEqual(first[0],dots(await fs.readFile(working.light.fsPath,'utf8'))[0]);
  const colors=[];
  for(const engine of ['codex','claude','agy','opencode'])colors.push(dots(await fs.readFile(icons.icon({engine,status:'done'}).dark.fsPath,'utf8'))[1]);
  assert.equal(new Set(colors).size,4);
 }finally{await fs.rm(root,{recursive:true});}
});

test('unknown activity preserves a confirmed provider, and a normal terminal differs from an unidentified agent',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'ai-provider-view-'));
 try{
  const view=new AgentsView(vscode(),{},()=>{},undefined,root);
  for(const [engine,label] of [['codex','Codex'],['claude','Claude'],['agy','AGY'],['opencode','OpenCode']]){
   const item=view.getTreeItem({key:'fixture',name:'Chosen terminal',engine,status:'unknown'});
   assert.ok(item.description.startsWith(label+' · '));assert.match(item.tooltip,/Punto izquierdo: actividad/);
   assert.match(item.accessibilityInformation.label,new RegExp(label));
   assert.ok(item.iconPath.dark.fsPath.endsWith('.svg'));assert.deepEqual(item.command.arguments,['fixture']);
  }
  const shell=view.getTreeItem({key:'shell',name:'Shell',status:'off'});
  const uncertain=view.getTreeItem({key:'ambiguous',name:'Shell',status:'unknown'});
  assert.match(shell.description,/^Terminal · /);assert.match(uncertain.description,/^Agente sin identificar · /);
  assert.notEqual(dots(await fs.readFile(shell.iconPath.dark.fsPath,'utf8'))[1],'none');
  assert.equal(dots(await fs.readFile(uncertain.iconPath.dark.fsPath,'utf8'))[1],'none');
 }finally{await fs.rm(root,{recursive:true});}
});

test('theme changes and explicit colors redraw icons without SVG injection or changing the provider state',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'ai-provider-theme-'));
 try{
  const overrides={'aiCommandAgents.providerClaude':'#123456'};
  const api=vscode(overrides);const icons=new ProviderIcons(api,root);const row={engine:'claude',status:'working'};
  assert.equal(dots(await fs.readFile(icons.icon(row).dark.fsPath,'utf8'))[1],'#123456');
  overrides['aiCommandAgents.providerClaude']='\"/><script>bad</script>';
  const safe=await fs.readFile(icons.icon(row).dark.fsPath,'utf8');assert.ok(!safe.includes('script'));
  const normal=icons.icon(row).dark.fsPath;api.window.activeColorTheme.kind=3;
  assert.notEqual(icons.icon(row).dark.fsPath,normal);
  assert.deepEqual(row,{engine:'claude',status:'working'});
 }finally{await fs.rm(root,{recursive:true});}
});

test('native AGY and OpenCode are recognized behind launchers without mistaking Python names or subagents',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'ai-provider-process-'));
 try{
  for(const engine of ['agy','opencode']){
   const base=path.join(root,engine);await fs.mkdir(base);
   const python=path.join(base,'python3');await fs.writeFile(python,'\x7fELFpython');
   const binary=path.join(base,engine);await fs.writeFile(binary,'\x7fELFnative');
   for(const [pid,exe,children] of [[10,python,'11'],[11,binary,'12']]){
    const dir=path.join(base,String(pid));await fs.mkdir(path.join(dir,'task',String(pid)),{recursive:true});
    await fs.writeFile(path.join(dir,'comm'),engine+'\n');await fs.symlink(exe,path.join(dir,'exe'));
    await fs.writeFile(path.join(dir,'task',String(pid),'children'),children);
   }
   assert.deepEqual(await findAgents(10,0,base),[{pid:11,engine}]);
   await fs.writeFile(path.join(base,'10/task/10/children'),'');
   assert.deepEqual(await findAgents(10,0,base),[]);
  }
 }finally{await fs.rm(root,{recursive:true});}
});
