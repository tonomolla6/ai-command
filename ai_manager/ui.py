"""ANSI styling that remains readable without color, in pipes, and in JSON."""
import os
import re
import shutil
import sys
import textwrap
import unicodedata
from itertools import zip_longest


PALETTE={'codex':'36','claude':'35','agy':'34','opencode':'33','ok':'32','low':'33','empty':'31','muted':'90','bold':'1','title':'1;36'}


def colored(text,style,mode=None):
    choice=mode or os.environ.get('AI_MANAGER_COLOR','auto')
    enabled=choice=='always' or (choice!='never' and sys.stdout.isatty() and 'NO_COLOR' not in os.environ)
    return '\x1b['+PALETTE.get(style,style)+'m'+str(text)+'\x1b[0m' if enabled else str(text)


def bar(value,blocked=False):
    if not isinstance(value,(int,float)):return colored('░'*12,'muted')+' UNKNOWN'
    count=max(0,min(12,round(value/100*12)))
    style='empty' if blocked else 'ok' if value>=25 else 'low' if value>0 else 'empty'
    return colored('█'*count+'░'*(12-count),style)+' '+colored(f'{value:g}% disponible',style)


def heading(text,provider=None):
    print('\n'+colored('╭─ '+text+' '+'─'*max(1,70-len(text)),provider or 'title'))


ANSI = re.compile(r'\x1b\[[0-9;]*m')


def visible_width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1
               for c in ANSI.sub('',text))


def fit(text,width):
    """Pad/truncate terminal cells while preserving ANSI style boundaries."""
    if visible_width(text)<=width:return text+' '*(width-visible_width(text))
    output=[];used=0
    for token in re.findall(r'\x1b\[[0-9;]*m|.',text):
        cells=0 if ANSI.fullmatch(token) else visible_width(token)
        if used+cells>width-1:break
        output.append(token);used+=cells
    return ''.join(output)+('\x1b[0m' if ANSI.search(text) else '')+'…'+' '*(width-used-1)


def credits_text(credits):
    if isinstance(credits,list):
        result=[]
        for item in credits:
            balance='Ilimitados' if item.get('unlimited') else (
                f"{float(item['balance']):,.2f} créditos" if item.get('balance') is not None else 'UNKNOWN')
            result.append(balance+(' ['+item['bucket']+']' if len(credits)>1 else ''))
        return result or ['UNKNOWN']
    if isinstance(credits,dict):
        return [credits.get('display') or ((credits.get('currency_symbol') or '')+str(credits['balance'])
                                          if credits.get('balance') is not None else 'UNKNOWN')]
    return ['UNKNOWN']


def window_name(window):
    name=window['name'].replace('Current session','Sesión / 5h').replace('Current week','Semanal')
    return (name.replace('codex/weekly','Semanal').replace('codex/5h','5h').replace('/weekly',' · semanal')
            .replace('base_model_inference · semanal','Semanal · Base model').replace('codex/43200m','Ventana 30 días'))


def blocked_windows(provider,windows):
    exhausted=[w for w in windows if isinstance(w.get('available_percent'),(int,float)) and w['available_percent']<=0]
    if provider=='claude':
        global_limit=any('session' in w['name'].lower() or 'all models' in w['name'].lower() for w in exhausted)
        return windows if global_limit else exhausted
    if provider=='agy':
        # Gemini and Claude/GPT allowances are independent. Propagate a weekly
        # or 5h blocker inside its own family, never between both model families.
        groups={w['name'].rsplit(' · ',1)[0] for w in exhausted}
        return [w for w in windows if w['name'].rsplit(' · ',1)[0] in groups]
    return windows if exhausted else []


def compact_tokens(value):
    for size,suffix in ((1_000_000_000,'G'),(1_000_000,'M'),(1000,'k')):
        if value>=size:return f'{value/size:.1f}{suffix}'
    return str(value)


def activity_lines(activity):
    result=[colored('Actividad local · referencia','bold')]
    for window in activity.get('windows',[]):
        result.append(f"{window['name']}: {compact_tokens(window['tokens']['total'])} tokens")
        value=window.get('load_percent')
        count=max(0,min(12,round((value or 0)/100*12)))
        result.append(colored('█'*count+'░'*(12-count),'title')+' '+
                      (f'≈{value:g}% del pico local' if value is not None else 'sin referencia'))
    errors=activity.get('errors',{})
    result.append(colored('Incluye caché y razonamiento','muted'))
    result.append(f"429 históricos: {activity.get('errors_429',0)} · otros: {sum(errors.values())-activity.get('errors_429',0)}")
    if activity.get('last_activity_at'):
        import datetime as dt
        try:date=dt.datetime.fromisoformat(activity['last_activity_at']).astimezone().strftime('%d/%m %H:%M %Z')
        except ValueError:date=activity['last_activity_at']
        result.append(colored('Último uso local: '+date,'muted'))
    result.append(colored('Referencia histórica; cuota UNKNOWN','muted'))
    return result


def usage_card(account,row,width,reset,age):
    provider=account['provider'];inner=width-4
    windows=row.get('windows',[])
    values=[w['available_percent'] for w in windows if isinstance(w.get('available_percent'),(int,float))]
    blocked=blocked_windows(provider,windows) if row.get('status')=='OK' else []
    state='SIN LOGIN' if row.get('reason')=='SIN LOGIN' else row.get('status','UNKNOWN')
    if state=='OK' and values:
        state='AGOTADO' if blocked and len(blocked)==len(windows) else 'LIMITADO' if blocked else 'BAJO' if min(values)<25 else 'OK'
    style='ok' if state=='OK' else 'empty' if state=='AGOTADO' else 'low'
    prefix={'codex':'x','claude':'c','agy':'agy','opencode':'oc'}[provider]
    title=(prefix if account.get('single') else prefix+account['account'])+' · '+account['label']
    result=[colored('┌─ '+fit(title,width-5)+' ┐',provider)]
    email=row.get('email') or account.get('email') or ('Cuenta actual · correo no publicado' if account.get('single') else 'correo pendiente')
    # A fresh failed identity check overrides an older registration check.
    verified=row.get('email_verified',account.get('email_verified',False))
    body=[colored(email,'bold')+colored(' ✓' if verified else ' ○','ok' if verified else 'muted'),colored(state,style)]
    if account.get('priority')=='low':body.append(colored('↓ Prioridad baja · última opción automática','low'))
    if blocked:
        exhausted=[window_name(w) for w in windows if isinstance(w.get('available_percent'),(int,float)) and w['available_percent']<=0]
        message=('Bloqueada por: ' if state=='AGOTADO' else 'Límite agotado: ')+', '.join(exhausted)
        body.extend(colored(line,'empty') for line in textwrap.wrap(message,width=inner))
    if account.get('fixed_model'):
        body.append(colored((account.get('plan_label') or '')+' · '+account['fixed_model'].replace('gpt-6-luna','Luna 6')+
                            (' · manual' if account.get('automatic') is False else ''),'low'))
    session=[w for w in windows if w.get('window_minutes')==300 or 'session' in w['name'].lower()]
    weekly=[w for w in windows if w.get('window_minutes')==10080 or 'week' in w['name'].lower()]
    if provider=='opencode' and row.get('activity'):body.append(colored('Cuota oficial: UNKNOWN','muted'))
    else:
        if not session:body.append(colored('5h / sesión: '+('No publicado' if windows else 'UNKNOWN'),'muted'))
        if not weekly:body.append(colored('Semanal: '+('No publicado' if windows else 'UNKNOWN'),'muted'))
    ordered=session+weekly+[w for w in windows if w not in session+weekly]
    model_headers={}
    if provider=='agy' and row.get('models'):
        ordered=[]
        groups={w['name'].rsplit(' · ',1)[0] for w in windows}
        for group in sorted(groups,key=lambda g:(not g.lower().startswith('gemini'),g)):
            members=[w for w in windows if w['name'].rsplit(' · ',1)[0]==group]
            members.sort(key=lambda w:w.get('window_minutes') or 999999)
            relevant=[m['name'] for m in row['models']
                      if (group=='Gemini Models' and any(i.startswith('gemini-') for i in m['ids'])) or
                         (group=='Claude and GPT models' and any(i.startswith(('claude-','gpt-')) for i in m['ids']))]
            if relevant and members:model_headers[id(members[0])]=relevant
            ordered.extend(members)
    for window in ordered:
        if id(window) in model_headers:
            names=', '.join(name.removeprefix('Gemini ').removeprefix('Claude ') for name in model_headers[id(window)])
            body.extend(colored(line,provider) for line in textwrap.wrap(names,width=inner))
        family=window['name'].rsplit(' · ',1)[0]
        known_family=provider=='agy' and row.get('models') and family in ('Gemini Models','Claude and GPT models')
        name=(window['name'].rsplit(' · ',1)[1]+' · compartido') if known_family else window_name(window)
        value=window.get('available_percent')
        is_blocked=window in blocked
        label=(f'{value:g}% · '+('AGOTADO' if value<=0 else 'BLOQUEADO') if is_blocked else f'{value:g}% disponible') if isinstance(value,(int,float)) else 'UNKNOWN'
        style='empty' if is_blocked else 'ok' if isinstance(value,(int,float)) and value>=25 else 'low' if value else 'empty'
        if visible_width(name)+len(label)+1>inner:
            body.append(name);body.append(colored(label,style))
        else:body.append(fit(name,inner-len(label)-1)+' '+colored(label,style))
        meter=bar(value,blocked=is_blocked).split(' ')[0]
        reset_line=colored('↻ '+reset(window),'muted')
        if visible_width(meter+' '+reset_line)<=inner:body.append(meter+' '+reset_line)
        else:body.extend([meter,reset_line])
    for value in credits_text(row.get('credits')):
        body.append(colored('Créditos: ','bold')+colored(value,'ok' if value!='UNKNOWN' else 'muted'))
    resets=row.get('reset_credits_available')
    if type(resets) is int and resets>=0:
        reset_text=f"{resets} "+('disponible' if resets==1 else 'disponibles')
        reset_style='ok' if resets else 'low'
    else:
        reset_text='No publicado por CLI' if row.get('status')=='OK' and resets is None else 'UNKNOWN'
        reset_style='muted'
    body.append(colored('Resets: ','bold')+colored(reset_text,reset_style))
    if row.get('activity'):body.extend(activity_lines(row['activity']))
    if row.get('free_models'):
        body.append(colored(f"{len(row['free_models'])} modelos FREE · catálogo",'ok'))
        for model in row['free_models']:
            body.append(colored('· '+model.get('name',model['id']),'opencode'))
    elapsed=age(row)
    queried=row.get('queried_at','sin consulta')
    try:
        import datetime as dt
        queried=dt.datetime.fromisoformat(queried).astimezone().strftime('%d/%m %H:%M:%S %Z')
    except (TypeError,ValueError):pass
    body.append(colored('Consulta: '+queried,'muted'))
    if elapsed is not None and elapsed>=120:body.append(colored(f'STALE · hace {elapsed}s; usa --refresh','low'))
    if row.get('reason') and row['reason']!='SIN LOGIN':
        body.extend(colored(line,'low') for line in textwrap.wrap(row['reason'],width=inner))
    for field in ('models_reason','credits_reason','activity_reason'):
        if row.get(field):body.extend(colored(line,'low') for line in textwrap.wrap(row[field],width=inner))
    for line in body:result.append(colored('│ ',provider)+fit(line,inner)+colored(' │',provider))
    result.append(colored('└'+'─'*(width-2)+'┘',provider))
    return result


def render_usage(accounts,rows,reset,age,layout='auto'):
    width=max(40,shutil.get_terminal_size(fallback=(120,24)).columns)
    columns=(layout=='columns' or (layout=='auto' and width>=108)) and len({a['provider'] for a in accounts})>1
    print(colored('AI USAGE','title')+'  '+colored('disponibilidad · sin turnos al modelo','muted'))
    def group(provider,card_width):
        title={'codex':'OPENAI / CODEX','claude':'ANTHROPIC / CLAUDE','agy':'AGY / ANTIGRAVITY','opencode':'OPENCODE / FREE'}[provider]
        if not any(a['provider']==provider for a in accounts):return []
        result=[colored(fit(title,card_width),provider),'']
        for account,row in zip(accounts,rows):
            if account['provider']==provider:
                result.extend(usage_card(account,row,card_width,reset,age));result.append('')
        return result
    if columns:
        card_width=(width-6)//3
        third=group('agy',card_width)+group('opencode',card_width)
        for left,middle,right in zip_longest(group('codex',card_width),group('claude',card_width),third,fillvalue=''):
            print(fit(left,card_width)+'   '+fit(middle,card_width)+'   '+right)
    else:
        for provider in ('codex','claude','agy','opencode'):
            for line in group(provider,width):print(line)
    print(colored('✓ correo verificado · caché 120s · --refresh actualiza','muted'))
