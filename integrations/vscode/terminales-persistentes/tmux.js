'use strict';

const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { randomBytes } = require('node:crypto');
const { readFile } = require('node:fs/promises');
const execute = promisify(execFile);

class Tmux {
  constructor(env = process.env) {
    this.env = { ...env, TMUX: '', TMUX_PANE: '' };
    this.sockets=env.AI_COMMAND_TMUX_SOCKET?[env.AI_COMMAND_TMUX_SOCKET]:['default','ai-command'];
  }

  async run(args, allowMissing = false, socket = this.sockets[0]) {
    try {
      const { stdout } = await execute('tmux', ['-L',socket,...args], {
        env: this.env, timeout: 3000, maxBuffer: 1024 * 1024,
      });
      return stdout.trimEnd();
    } catch (error) {
      if (allowMissing && /no server running|No such file or directory/.test(error.stderr || '')) return '';
      throw error;
    }
  }

  async sessions() {
    const format = '#{session_id}\t#{session_created}\t#{session_name}\t#{@ai_command_vscode_label}\t#{@ai_command_auto_restore}\t#{@ai_command_vscode_workspace}\t#{@ai_command_vscode_order}\t#{@ai_command_vscode_key}\t#{@ai_command_vscode_launcher_pid}\t#{@ai_command_vscode_group}';
    const all=await Promise.all(this.sockets.map(async socket=>{
    const text = await this.run(['list-sessions', '-f', '#{==:#{@ai_command_interactive},1}', '-F', format], true,socket);
    return text.split('\n').filter(Boolean).map(line => {
      const [id, created, name, label = '', restore, workspace = '', order, key = '', launcher, group] = line.split('\t');
      return { id, ref:socket+':'+id,socket, created, name, label, restore: restore !== '0', workspace, key, launcherPid:Number(launcher)||undefined,
        group:/^\d+$/.test(group||'')?Number(group):undefined,
        order: /^\d+$/.test(order || '') ? Number(order) : Number.MAX_SAFE_INTEGER };
    }).filter(row => /^\$\d+$/.test(row.id) && /^\d+$/.test(row.created));
    }));return all.flat();
  }

  async clients() {
    const all=await Promise.all(this.sockets.map(async socket=>{
    const text = await this.run(['list-clients', '-F', '#{client_pid}\t#{session_id}'], true,socket);
    return text.split('\n').filter(Boolean).map(line => {
      const [pid, id] = line.split('\t');
      return [Number(pid), socket+':'+id];
    });}));const clients=new Map(all.flat());
    for (const [pid, id] of [...clients]) {
      try {
        const status = await readFile(`/proc/${pid}/status`, 'utf8');
        const parent = Number(status.match(/^PPid:\s+(\d+)/m)?.[1]);
        if (!parent) continue;
        const command = await readFile(`/proc/${parent}/cmdline`, 'utf8');
        if (command.split('\0').some(arg => arg.endsWith('/terminales-native'))) clients.set(parent, id);
      } catch (error) {
        if (error.code !== 'ENOENT' && error.code !== 'ESRCH') throw error;
      }
    }
    return clients;
  }

  async nativeConnected(session) {
    const text = await this.run(['list-clients', '-t', session.id, '-F', '#{client_pid}'], true,session.socket);
    for (const pid of text.split('\n').filter(Boolean)) {
      try {
        const status = await readFile(`/proc/${pid}/status`, 'utf8');
        const parent = Number(status.match(/^PPid:\s+(\d+)/m)?.[1]);
        if (!parent) continue;
        const command = await readFile(`/proc/${parent}/cmdline`, 'utf8');
        if (command.split('\0').some(arg => arg.endsWith('/terminales-native'))) return true;
      } catch (error) {
        if (error.code !== 'ENOENT' && error.code !== 'ESRCH') throw error;
      }
    }
    return false;
  }

  async set(session, option, value) {
    try {
      await this.run(['set-option', '-t', session.id, option, String(value)],false,session.socket);
    } catch (error) {
      // Una shell puede terminar mientras VS Code notifica su cierre.
      if (!/can't find session|no server running|No such file or directory/.test(error.stderr || '')) throw error;
    }
  }

  async kill(session) {
    if (!/^\$\d+$/.test(session.id) || !/^\d+$/.test(session.created) ||
        (session.key && !/^[a-f0-9]{32}$/.test(session.key))) {
      throw new Error('Identidad tmux no valida para cerrar la terminal.');
    }
    // Evaluate identity and kill in the same tmux command queue, without a shell.
    // IDs can be reused after server restart; creation time/key must still match.
    let matches = `#{&&:#{==:#{session_id},${session.id}},#{==:#{session_created},${session.created}}}`;
    matches = `#{&&:${matches},#{==:#{@ai_command_interactive},1}}`;
    if (session.key) matches = `#{&&:${matches},#{==:#{@ai_command_vscode_key},${session.key}}}`;
    try {
      await this.run(['if-shell', '-F', '-t', session.id + ':', matches,
        'kill-session -t ' + session.id],false,session.socket);
    } catch (error) {
      if (!/can't find session|no server running|No such file or directory/.test(error.stderr || '')) throw error;
    }
  }

  async ticket(session) {
    const token = randomBytes(16).toString('hex');
    await this.run(['set-option', '-g', '@ai_command_resume_' + token, session.id + ':' + session.created],false,session.socket);
    return 'vscode-' + token;
  }

  async persistentKey(session) {
    if (/^[a-f0-9]{32}$/.test(session.key || '')) return session.key;
    const key = randomBytes(16).toString('hex');
    await this.set(session, '@ai_command_vscode_key', key);
    session.key = key;
    return key;
  }
}

module.exports = { Tmux };
