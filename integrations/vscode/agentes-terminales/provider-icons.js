'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {createHash}=require('node:crypto');
const definitions=new Map(require('./package.json').contributes.colors.map(color=>[color.id,color.defaults]));

const PROVIDERS={
  codex:{label:'Codex',color:'providerCodex'},
  claude:{label:'Claude',color:'providerClaude'},
  agy:{label:'AGY',color:'providerAgy'},
  opencode:{label:'OpenCode',color:'providerOpencode'},
  terminal:{label:'Terminal',color:'providerTerminal'},
  unknown:{label:'Agente sin identificar',color:'unknown'},
};
function providerOf(row){
  return PROVIDERS[row.engine]?row.engine:row.status==='off'?'terminal':'unknown';
}
function color(vscode,name,theme){
  const configuration=vscode.workspace?.getConfiguration?.('workbench');
  const overrides=configuration?.get('colorCustomizations')||{};
  const themeName=configuration?.get('colorTheme');
  const id='aiCommandAgents.'+name;
  const value=overrides['['+themeName+']']?.[id]||overrides[id];
  // Only a color value can enter the SVG; arbitrary workspace strings cannot.
  return typeof value==='string'&&/^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(value)?value:
    definitions.get(id)?.[theme]||'#8A8A8A';
}
function svg(status,provider,hollow){
  return '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'+
    '<circle cx="4" cy="8" r="3.25" fill="'+status+'"/>'+
    '<circle cx="12" cy="8" r="3.25" fill="'+(hollow?'none':provider)+'"'+
    (hollow?' stroke="'+provider+'" stroke-width="1.5"':'')+'/></svg>';
}

class ProviderIcons {
  constructor(vscode,root){this.vscode=vscode;this.root=root;this.cache=new Map();}
  icon(row){
    const provider=providerOf(row);
    const highContrast=[3,4].includes(this.vscode.window?.activeColorTheme?.kind);
    const variants={light:highContrast?'highContrastLight':'light',dark:highContrast?'highContrast':'dark'};
    const contents=Object.fromEntries(Object.entries(variants).map(([key,theme])=>
      [key,svg(color(this.vscode,row.status,theme),color(this.vscode,PROVIDERS[provider].color,theme),provider==='unknown')]));
    const id=createHash('sha256').update(JSON.stringify(contents)).digest('hex').slice(0,20);
    if(this.cache.has(id))return this.cache.get(id);
    fs.mkdirSync(this.root,{recursive:true,mode:0o700});
    const result={};
    for(const [variant,content] of Object.entries(contents)){
      const filename=path.join(this.root,id+'-'+variant+'.svg');
      try{fs.writeFileSync(filename,content,{mode:0o600,flag:'wx'});}
      catch(error){if(error.code!=='EEXIST')throw error;}
      result[variant]=this.vscode.Uri.file(filename);
    }
    this.cache.set(id,result);
    return result;
  }
}
module.exports={ProviderIcons,PROVIDERS,providerOf};
