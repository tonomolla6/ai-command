'use strict';

// Local Codex 0.153.x event adapter. No transcript text is retained or displayed.
function rootSession(record) {
  const p = record?.payload;
  return record?.type === 'session_meta' && ['cli','vscode'].includes(p?.source) && !p.parent_thread_id
    ? p.id || p.session_id : undefined;
}

class State {
  constructor() { this.status = 'unknown'; this.turn = undefined; this.startedAt = 0; this.pending = new Set(); this.asyncPending = new Set(); }
  accept(record) {
    const p = record?.payload;
    if (!p) return;
    if (record.type === 'event_msg') {
      if (p.type === 'task_started') {
        this.turn = p.turn_id; this.startedAt = Date.parse(record.timestamp) || 0;
        this.pending.clear(); this.asyncPending.clear(); this.status = 'working';
      } else if (p.type === 'task_complete' && (!this.turn || this.turn === p.turn_id)) {
        this.status = this.pending.size ? 'attention' : 'done';
      } else if (['turn_aborted', 'task_failed'].includes(p.type)) {
        this.pending.clear(); this.asyncPending.clear(); this.status = 'attention';
      }
    }
    if (record.type === 'response_item') {
      if (p.type === 'function_call' && /^(?:functions\.)?request_user_input(?:_async)?$/.test(p.name)) {
        this.pending.add(p.call_id);
        if(p.name.endsWith('_async'))this.asyncPending.add(p.call_id);
        this.status=this.pending.size>this.asyncPending.size?'attention':'working';
      } else if (p.type === 'function_call_output' && !this.asyncPending.has(p.call_id) && this.pending.delete(p.call_id)) {
        this.status=this.pending.size>this.asyncPending.size?'attention':'working';
      } else if(p.type==='message' && p.role==='user' && this.asyncPending.size){
        for(const id of this.asyncPending)this.pending.delete(id);
        this.asyncPending.clear();this.status=this.pending.size?'attention':'working';
      }
    }
  }
  result(mark, pid) {
    const valid = mark && mark.pid === pid && mark.at >= this.startedAt &&
      ['working','done','attention'].includes(mark.state);
    return {status: this.status==='working'?'working':valid?mark.state:this.status, manual: !!valid};
  }
}

function attentionScreen(screen) {
  // Only the live bottom of a pane, with both a prompt and its choice controls.
  const bottom = screen.split('\n').slice(-22).join('\n');
  return /Would you like to (?:run|make)|Do you want to allow|Approve this|Allow .*access/i.test(bottom) &&
    /Yes, (?:proceed|allow)|No, and tell Codex|Press enter to confirm or esc to cancel/i.test(bottom);
}

function visibleStatus(status, screen) {
  if(attentionScreen(screen))return 'attention';
  const bottom=screen.trimEnd().split('\n').slice(-14).join('\n');
  if(/^\s*[•●]\s.*\besc to interrupt\b/im.test(bottom))return 'working';
  return status;
}

module.exports = { State, rootSession, attentionScreen, visibleStatus };
