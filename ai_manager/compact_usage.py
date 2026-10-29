"""Compact provider blocks; the detailed cards remain available on request."""
import math
import textwrap

from .availability import instant
from .ui import ANSI, agy_family_models, blocked_windows, colored, compact_tokens, credits_text, fit, visible_width, window_name


def _label(window):
    name=window.get('name','?');lower=name.lower()
    if 'fable' in lower:return 'Fable'
    if 'base_model' in lower:return 'Base'
    if window.get('window_minutes')==300 or 'session' in lower or lower.endswith('/5h') or lower.endswith(' · 5h'):return '5h'
    if window.get('window_minutes')==10080 or 'week' in lower or 'semanal' in lower:return '7d'
    if '43200m' in lower or window.get('window_minutes')==43200:return '30d'
    return window_name(window)


def _pack(items,width,separator=' · '):
    lines=[];current=''
    for item in items:
        if visible_width(item)>width and not ANSI.search(item):
            if current:lines.append(current);current=''
            lines.extend(textwrap.wrap(item,width=max(1,width),break_on_hyphens=False))
            continue
        if current and visible_width(current+separator+item)>width:
            lines.append(current);current=''
        current=current+separator+item if current else item
    if current:lines.append(current)
    return lines


def _short_reset(window,reference):
    reset=instant(window.get('reset_at'))
    if reset:
        return reset.astimezone().strftime('%H:%M' if reset.astimezone().date()==reference.astimezone().date() else '%d/%m %H:%M')
    return window.get('reset_display') or '?'


def _window_lines(windows,provider,width,reference):
    blocked=blocked_windows(provider,windows)
    metrics=[];resets={}
    for window in windows:
        label=_label(window)
        value=window.get('available_percent')
        valid=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
        is_blocked=window in blocked
        text=f'{value:g}%'+('!' if is_blocked and value>0 else '') if valid else 'UNKNOWN'
        style='empty' if is_blocked else 'ok' if valid and value>=25 else 'low' if valid else 'muted'
        short=fit(label,min(12,max(3,width-len(text)-1))).rstrip()
        metrics.append((short,text,style,value if valid else None))
        # Weekly model and global allowances often reset together. Keep both
        # labels, sharing their timestamp rather than printing the date twice.
        reset=_short_reset(window,reference)
        resets.setdefault(reset,[]).append(short.replace('Fable','F'))
    def values(size):
        result=[]
        for label,text,style,value in metrics:
            if value is None:result.append(colored(label+' '+text,style));continue
            filled=round(max(0,min(100,value))/100*size)
            result.append(colored(label+' '+'█'*filled+'░'*(size-filled)+text,style))
        return result
    # Spend spare horizontal space on bars, keeping three model windows on
    # one row in a narrow column. Exact percentages remain beside every bar.
    size=6
    while size>3 and visible_width(' '.join(values(size)))>width:size-=1
    lines=_pack(values(size),width,separator=' ')
    reset_parts=['+'.join(dict.fromkeys(labels))+' '+value for value,labels in resets.items()]
    lines.extend(colored('↻ '+line,'muted') for line in _pack(reset_parts,max(1,width-2)))
    return lines


def _models(row,family,families):
    names=agy_family_models(row.get('models',[]),family,families)
    return ', '.join(n.removeprefix('Gemini ').removeprefix('Claude ') for n in names) or family


def compact_account(account,row,width,reference,age):
    inner=width-2;provider=account['provider']
    prefix={'codex':'x','claude':'c','agy':'agy','opencode':'oc'}[provider]
    alias=prefix+account['account'] if provider in ('codex','claude') else prefix
    if account.get('automatic') is False:alias+=' [M]'
    if account.get('priority')=='low':alias+=' ↓'
    email=row.get('email') or account.get('email') or 'cuenta actual'
    verified=row.get('email_verified',False)
    heading=alias+' '+email+(' ✓' if verified else ' ○')
    body=[colored(line,'bold') for line in textwrap.wrap(heading,width=inner)] if not account.get('single') or row.get('email') or account.get('email') else []
    windows=row.get('windows',[])
    metrics_at=len(body)
    if row.get('status')=='OK' and windows:
        if provider=='agy':
            families=dict.fromkeys(w['name'].rsplit(' · ',1)[0] for w in windows)
            for family in families:
                body.extend(colored(line,provider) for line in textwrap.wrap(_models(row,family,families),width=inner))
                body.extend(_window_lines([w for w in windows if w['name'].rsplit(' · ',1)[0]==family],provider,inner,reference))
        else:body.extend(_window_lines(windows,provider,inner,reference))
    elif not (provider=='opencode' and row.get('activity')):
        state=row.get('status','UNKNOWN')
        if state=='OK' and not windows:state='UNKNOWN'
        body.append(colored('Cuotas: '+state,'empty' if state=='SIN LOGIN' else 'muted'))
        if state=='SIN LOGIN':body.append(colored('ai login '+provider+' '+account['account'],'low'))
    if account.get('fixed_model'):
        body.extend(colored(line,'muted') for line in textwrap.wrap(
            ((account.get('plan_label') or '')+' '+account['fixed_model'].replace('gpt-6-luna','Luna 6')+
             (' · manual' if account.get('automatic') is False else '')).strip(),width=inner))
    extras=[]
    for value in credits_text(row.get('credits')):
        value=value.replace('Desactivados','OFF').replace('UNKNOWN','?').replace(' créditos','')
        extras.append('Cr '+value)
    resets=row.get('reset_credits_available')
    if provider=='codex' or type(resets) is int and resets>=0:
        extras.append('R '+(str(resets) if type(resets) is int and resets>=0 else
                            '—' if resets is None and row.get('status')=='OK' else '?'))
    activity=row.get('activity')
    if activity:extras.append('429: '+str(activity.get('errors_429',0)))
    queried=instant(row.get('queried_at'))
    observed=instant(row.get('observed_at')) if row.get('source_type')=='claude_cache' else None
    if observed:extras.append('caché '+observed.astimezone().strftime('%H:%M'))
    elif queried:extras.append(('sesión ' if row.get('source_type')=='claude_statusline' else '')+'@'+queried.astimezone().strftime('%H:%M'))
    elapsed=age(row)
    if elapsed is not None and elapsed>=120:extras.append('STALE')
    remaining=[]
    for item in extras:
        # Reuse quota/reset rows so bars do not add a metadata row per account.
        # Prefer credits beside percentages and timestamps beside reset dates.
        targets=range(metrics_at,len(body)) if item.startswith('Cr ') else range(len(body)-1,metrics_at-1,-1)
        target=next((index for index in targets if visible_width(body[index]+' · '+item)<=inner),None)
        if provider in ('codex','claude') and row.get('status')=='OK' and windows and target is not None:
            body[target]+=' · '+colored(item,'muted')
        else:remaining.append(item)
    body.extend(colored(line,'muted') for line in _pack(remaining,inner))
    if activity:
        body.append(colored('actividad local · % pico (no cuota)','muted'))
        values=[]
        for window in activity.get('windows',[]):
            value=window.get('load_percent')
            values.append(window['name']+' '+compact_tokens(window['tokens']['total'])+
                          (f' ≈{value:g}%' if isinstance(value,(int,float)) else ''))
        body.extend(colored(line,'opencode') for line in _pack(values,inner))
    if row.get('free_models'):
        body.append(colored(str(len(row['free_models']))+' modelos FREE · ai models opencode','opencode'))
    if row.get('reason') and row.get('status')!='SIN LOGIN':
        body.append(colored(fit(row['reason'],inner).rstrip(),'low'))
    return [colored('│ ',provider)+fit(line,inner) for line in body]
