'use strict';

const { randomBytes } = require('node:crypto');
const path = require('node:path');

const ROOT = process.env.AI_COMMAND_WORKSPACE_ROOT || require('node:os').homedir();
// A relative shellPath can be replaced by VS Code's default profile during
// resolution. Own the helper pair and preserve the exact persistent key.
const LAUNCHER = process.env.AI_COMMAND_TERMINALES_TEST_LAUNCHER || path.join(__dirname, 'bin', 'terminales');
const identity = session => session.id + ':' + session.created;
const clean = value => String(value || '').replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, 200).trim();

function workspaceRoots(vscode) {
  const roots = (vscode.workspace?.workspaceFolders || [])
    .map(folder => folder.uri?.fsPath)
    .filter(path => typeof path === 'string' && path.length > 0);
  return roots.length ? roots : [ROOT];
}

function processIdWithin(terminal) {
  let timer;
  return Promise.race([
    terminal.processId,
    new Promise(resolve => { timer = setTimeout(() => resolve(undefined), 500); }),
  ]).finally(() => clearTimeout(timer));
}

class Controller {
  constructor(vscode, backend, report) {
    this.vscode = vscode;
    this.backend = backend;
    this.report = report;
    this.tracked = new Map();
    this.closed = new WeakSet();
    this.syncing = undefined;
    this.restoring = undefined;
  }

  workspaceRoot() {
    return workspaceRoots(this.vscode)[0];
  }

  newOptions(key = randomBytes(16).toString('hex'), recovering = false) {
    return {
      name: 'Terminal', shellPath: LAUNCHER, shellArgs: [(recovering ? 'vsc-resume-' : 'vsc-tab-') + key], cwd: this.workspaceRoot(),
      env: { TMUX: null, TMUX_PANE: null, NO_COLOR: null, AI_COMMAND_TERMINALES_NATIVE: '1',
        AI_COMMAND_TERMINAL_CWD: this.workspaceRoot() },
      isTransient: true,
    };
  }

  allowed(session) {
    return workspaceRoots(this.vscode).includes(session.workspace || ROOT);
  }

  async restoreLabel(terminal, label) {
    if (!label || terminal.name === label) return;
    const previous = this.vscode.window.activeTerminal;
    terminal.show(true);
    try {
      for (let attempt = 0; this.vscode.window.activeTerminal !== terminal && attempt < 100; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      if (this.vscode.window.activeTerminal !== terminal) throw new Error('No se pudo seleccionar la pestaña para recuperar su nombre.');
      await this.vscode.commands.executeCommand('workbench.action.terminal.renameWithArg', { name: label });
    } finally {
      if (previous && previous !== terminal && this.vscode.window.terminals.includes(previous)) previous.show(true);
    }
  }

  async save(terminal, record) {
    const label = clean(terminal.name) || record.session.name;
    if (label !== record.savedLabel) {
      await this.backend.set(record.session, '@ai_command_vscode_label', label);
      record.savedLabel = label;
    }
    const order = this.vscode.window.terminals.indexOf(terminal);
    if (order >= 0 && order !== record.savedOrder) {
      await this.backend.set(record.session, '@ai_command_vscode_order', order);
      record.savedOrder = order;
    }
  }

  sync() {
    if (this.syncing) return this.syncing;
    this.syncing = this.doSync().finally(() => { this.syncing = undefined; });
    return this.syncing;
  }

  async doSync() {
    const [sessions, clients] = await Promise.all([this.backend.sessions(), this.backend.clients()]);
    const byId = new Map(sessions.filter(session => this.allowed(session)).map(session => [session.id, session]));
    for (const [terminal, record] of this.tracked) {
      const current = byId.get(record.session.id);
      if (!current || identity(current) !== identity(record.session)) this.tracked.delete(terminal);
    }
    for (const terminal of this.vscode.window.terminals) {
      if (this.closed.has(terminal)) continue;
      let record = this.tracked.get(terminal);
      if (!record) {
        const pid = await processIdWithin(terminal);
        const arg = terminal.creationOptions?.shellArgs?.[0];
        const key = typeof arg === 'string' ? /^vsc-(?:tab|resume)-([a-f0-9]{32})$/.exec(arg)?.[1] || '' : '';
        const clientSessionId = clients.get(pid);
        // A visible terminal is authoritative after changing workspace: adopt
        // its existing tmux session into the workspace showing that tab.
        const session = byId.get(clientSessionId) ||
          (clientSessionId ? sessions.find(s => s.id === clientSessionId) : undefined) ||
          sessions.find(s => key && s.key === key && this.allowed(s));
        if (!session || this.closed.has(terminal)) continue;
        if (!this.allowed(session)) {
          if (!clientSessionId) continue;
          await this.backend.set(session, '@ai_command_vscode_workspace', this.workspaceRoot());
          session.workspace = this.workspaceRoot();
        }
        // Si VS Code revive tarde la pestaña original, conservar su grupo y
        // cerrar solamente el cliente adicional que hemos creado al recuperar.
        for (const [other, saved] of this.tracked) {
          if (other !== terminal && saved.recovered && identity(saved.session) === identity(session)) {
            this.closed.add(other);
            this.tracked.delete(other);
            if (this.vscode.window.activeTerminal === other) terminal.show(true);
            other.dispose();
          }
        }
        // Algunas pestañas revividas reciben el título original del perfil.
        // Recuperar la etiqueta guardada antes de permitir que sync la sobrescriba.
        if (session.key) await this.restoreLabel(terminal, session.label);
        record = { session, savedLabel: session.label, savedOrder: session.order };
        this.tracked.set(terminal, record);
        await this.backend.set(session, '@ai_command_vscode_workspace', this.workspaceRoot());
        await this.backend.set(session, '@ai_command_auto_restore', 1);
      }
      if (!this.closed.has(terminal)) await this.save(terminal, record);
    }
  }

  restore(legacyOnly = false) {
    if (this.restoring) return this.restoring;
    this.restoring = this.doRestore(legacyOnly).finally(() => { this.restoring = undefined; });
    return this.restoring;
  }

  start() {
    // Reconnection must only attach clients, never relaunch live terminals.
    return this.restore();
  }

  async doRestore(legacyOnly) {
    await this.sync();
    const represented = new Set([...this.tracked.values()].map(record => identity(record.session)));
    const sessions = (await this.backend.sessions()).filter(session =>
      this.allowed(session) && session.restore && (!legacyOnly || !session.key));
    sessions.sort((a, b) => a.order - b.order || a.name.localeCompare(b.name, undefined, { numeric: true }));
    let restored = 0;
    let first;
    for (const session of sessions) {
      if (represented.has(identity(session))) continue;
      const key = await this.backend.persistentKey(session);
      const terminal = this.vscode.window.createTerminal({
        ...this.newOptions(key, true), name: session.label || session.name,
      });
      this.tracked.set(terminal, { session, savedLabel: session.label, savedOrder: session.order, recovered: true });
      represented.add(identity(session));
      await this.backend.set(session, '@ai_command_vscode_workspace', this.workspaceRoot());
      first ||= terminal;
      restored++;
    }
    if (first) first.show(true);
    this.report(`Recuperadas ${restored} terminales; conservados los procesos tmux.`);
    return restored;
  }

  async migrate() {
    await this.sync();
    const previous = this.vscode.window.activeTerminal;
    for (const [terminal, record] of [...this.tracked]) {
      if (this.closed.has(terminal) || await this.backend.nativeConnected(record.session)) continue;
      const arg = terminal.creationOptions?.shellArgs?.[0];
      if (!(typeof arg === 'string' && /^vsc-(?:tab|resume)-[a-f0-9]{32}$/.test(arg))) {
        throw new Error('La pestaña ' + terminal.name + ' necesita primero una clave persistente.');
      }
      const label = terminal.name;
      await this.save(terminal, record);
      await this.backend.set(record.session, '@ai_command_native', 1);
      terminal.show(true);
      for (let attempt = 0; this.vscode.window.activeTerminal !== terminal && attempt < 100; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      if (this.vscode.window.activeTerminal !== terminal) throw new Error('No se pudo enfocar ' + terminal.name);
      // Relaunch changes the client process in place, preserving exact split layout.
      await this.vscode.commands.executeCommand('workbench.action.terminal.relaunch');
      let connected = false;
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await this.backend.nativeConnected(record.session)) { connected = true; break; }
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (!connected) {
        await this.backend.set(record.session, '@ai_command_native', 0);
        await this.vscode.commands.executeCommand('workbench.action.terminal.relaunch');
        throw new Error('No se pudo activar la conexion nativa de ' + terminal.name + '; se vuelve al cliente clasico.');
      }
      await this.restoreLabel(terminal, label);
      await this.backend.set(record.session, '@ai_command_vscode_label', label);
      record.savedLabel = label;
      await this.backend.set(record.session, '@ai_command_auto_restore', 1);
      this.report('Conexion nativa en la misma pestaña, proceso conservado: ' + terminal.name);
    }
    if (previous && this.vscode.window.terminals.includes(previous)) previous.show(true);
  }

  async recoverClosed(session) {
    if (this.disposed) return;
    await this.sync();
    if (this.disposed || [...this.tracked.values()].some(r => identity(r.session) === identity(session))) return;
    const current = (await this.backend.sessions()).find(s => identity(s) === identity(session));
    if (!current || !current.restore || !this.allowed(current) || this.disposed) return;
    this.recoveryAttempts ||= new Map();
    const now = Date.now();
    const attempts = (this.recoveryAttempts.get(identity(current)) || []).filter(t => now - t < 60000);
    if (attempts.length >= 3) {
      this.report('ERROR: Recuperacion pausada tras 3 caidas en un minuto: ' + current.name);
      return;
    }
    attempts.push(now);
    this.recoveryAttempts.set(identity(current), attempts);
    const key = await this.backend.persistentKey(current);
    if (this.disposed) return;
    const recovered = this.vscode.window.createTerminal({ ...this.newOptions(key, true), name: current.label || current.name });
    this.tracked.set(recovered, { session: current, savedLabel: current.label, savedOrder: current.order, recovered: true });
    this.report('Terminal recuperada tras salida del cliente; proceso conservado: ' + current.name);
  }

  async close(terminal) {
    // Internal disposal of a duplicate recovery tab must never kill its session.
    if (this.closed.has(terminal)) return;
    this.closed.add(terminal);
    if (this.syncing) await this.syncing;
    const record = this.tracked.get(terminal);
    this.tracked.delete(terminal);
    if (terminal.exitStatus?.reason === this.vscode.TerminalExitReason.User) {
      let session = record?.session;
      if (!session) {
        // A tab can be deleted before the first sync, after its client has exited.
        const arg = terminal.creationOptions?.shellArgs?.[0];
        const key = typeof arg === 'string' ? /^vsc-(?:tab|resume)-([a-f0-9]{32})$/.exec(arg)?.[1] || '' : '';
        if (key) session = (await this.backend.sessions()).find(s => s.key === key && this.allowed(s));
      }
      if (session) {
        await this.backend.kill(session);
        this.report('Cierre solicitado por el usuario: ' + session.name);
      }
    } else if (record) {
      // Shutdown, disconnect, relaunch and extension disposal retain persistence.
      await this.save(terminal, record);
      if (terminal.exitStatus?.reason === this.vscode.TerminalExitReason.Process &&
          this.vscode.workspace.getConfiguration('aiCommandTerminales').get('autoRestore', true)) {
        this.report('Salida inesperada del cliente: ' + record.session.name + ' code=' + terminal.exitStatus.code);
        await this.recoverClosed(record.session);
      }
    }
  }

  async flush() {
    if (this.syncing) await this.syncing;
    const existing = new Set((await this.backend.sessions()).map(identity));
    for (const [terminal, record] of this.tracked) {
      if (existing.has(identity(record.session))) await this.save(terminal, record);
    }
  }
}

module.exports = { Controller, ROOT };
