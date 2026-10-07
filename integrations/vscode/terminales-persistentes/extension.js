'use strict';

const vscode = require('vscode');
const { Controller, ROOT } = require('./controller');
const { Tmux } = require('./tmux');
let controller;
let timer;

async function activate(context) {
  if (process.platform !== 'linux') return;
  const output = vscode.window.createOutputChannel('AI Command Terminales');
  const report = message => output.appendLine(new Date().toISOString() + ' ' + message);
  const safely = promise => promise.catch(error => report('ERROR: ' + error.message));
  controller = new Controller(vscode, new Tmux(), report);
  context.subscriptions.push(output,
    vscode.window.registerTerminalProfileProvider('aiCommand.terminalesPersistentes', {
      provideTerminalProfile: () => new vscode.TerminalProfile(controller.newOptions()),
    }),
    vscode.commands.registerCommand('aiCommandTerminalesPersistentes.restore', () => safely(controller.restore())),
    vscode.window.onDidOpenTerminal(() => { safely(controller.sync()); }),
    vscode.window.onDidCloseTerminal(terminal => { safely(controller.close(terminal)); }),
    vscode.window.onDidChangeActiveTerminal(() => { safely(controller.sync()); }),
  );
  timer = setInterval(() => { safely(controller.sync()); }, 3000);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
  const hasWorkspace = (vscode.workspace.workspaceFolders || []).length > 0;
  if (hasWorkspace && vscode.workspace.getConfiguration('aiCommandTerminales').get('autoRestore', true)) {
    await controller.migrate();
    // Una clave tmux no demuestra que VS Code haya recuperado la pestaña.
    // Adoptar las visibles y recuperar todas las ausentes, tambien las con clave.
    await controller.restore();
    const config = vscode.workspace.getConfiguration('terminal.integrated');
    await config.update('enablePersistentSessions', true, vscode.ConfigurationTarget.Global);
    await config.update('persistentSessionReviveProcess', 'onExitAndWindowClose', vscode.ConfigurationTarget.Global);
    await config.update('rightClickBehavior', 'copyPaste', vscode.ConfigurationTarget.Global);
    await config.update('copyOnSelection', false, vscode.ConfigurationTarget.Global);
    await config.update('scrollback', 50000, vscode.ConfigurationTarget.Global);
    const skip = config.get('commandsToSkipShell', []).filter(command => command !== '-workbench.action.terminal.selectAll');
    if (!skip.includes('workbench.action.terminal.selectAll')) skip.push('workbench.action.terminal.selectAll');
    await config.update('commandsToSkipShell', skip, vscode.ConfigurationTarget.Global);
  }
  report('Integracion nativa activada: tmux conserva procesos; VS Code gestiona pantalla, scroll y seleccion.');
  return { ready: true, migrate: () => controller.migrate() };
}

async function deactivate() {
  clearInterval(timer);
  if (controller) controller.disposed = true;
  if (controller) await controller.flush();
}

module.exports = { activate, deactivate };
