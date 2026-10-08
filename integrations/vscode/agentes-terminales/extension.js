'use strict';
const vscode=require('vscode');
const {AgentsView}=require('./view');
const path=require('node:path');

exports.activate=async function(context){
  const out=vscode.window.createOutputChannel('AI Command Agentes');
  const provider=new AgentsView(vscode,undefined,message=>out.appendLine('ERROR: '+message),undefined,
    path.join(context.globalStorageUri.fsPath,'provider-icons'));
  const tree=vscode.window.createTreeView('aiCommandAgents.list',{treeDataProvider:provider,showCollapseAll:false});
  const refresh=async()=>{
    await provider.refresh();
    tree.description=provider.demo?'Demostración':provider.orderSynced?'Orden de VS Code · Clic: final':'Sincronizando orden…';
  };
  const timer=setInterval(refresh,1000);
  context.subscriptions.push(out,provider,tree,{dispose:()=>clearInterval(timer)},
    vscode.commands.registerCommand('aiCommandAgents.openTerminal',key=>provider.open(key)),
    vscode.commands.registerCommand('aiCommandAgents.refresh',async()=>{tree.message=undefined;await provider.live();}),
    vscode.commands.registerCommand('aiCommandAgents.demo',()=>{provider.showDemo();tree.message='Demostración. Pulsa Actualizar para volver a tus agentes.';}),
    vscode.workspace.onDidChangeConfiguration(event=>{
      if(event.affectsConfiguration('workbench.colorCustomizations')||event.affectsConfiguration('workbench.colorTheme'))provider.events.fire();
    }),
    vscode.window.onDidChangeActiveColorTheme(()=>provider.events.fire()),
    vscode.window.onDidOpenTerminal(refresh),vscode.window.onDidCloseTerminal(refresh));
  await refresh();
  out.appendLine(`Lista activa: ${provider.rows.length} terminales; consulta local cada segundo.`);
  if(!context.globalState.get('introduced')){
    await context.globalState.update('introduced',true);
    await vscode.commands.executeCommand('aiCommandAgents.list.focus');
  }
  return {provider,tree,refresh};
};
