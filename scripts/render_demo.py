#!/usr/bin/env python3
"""Render the real CLI with fictional fixtures. Requires Pillow for docs only."""
import contextlib
import io
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from ai_manager.ui import render_usage

accounts=[];rows=[]
def window(name,value,minutes):return {'name':name,'available_percent':value,'window_minutes':minutes}
def add(provider,number,email,windows,**extra):
 accounts.append({'provider':provider,'account':str(number),'label':provider.title()+' '+str(number),
                  'email':email,'email_verified':bool(email),'single':provider in ('agy','opencode')})
 rows.append({'status':'OK','email_verified':bool(email),'windows':windows,
              'credits':{'display':'Desactivados'},'queried_at':'2026-10-07T10:30:00+00:00',**extra})

add('codex',1,'ana@example.org',[window('codex/5h',34,300),window('codex/weekly',72,10080)],credits={'display':'120 créditos'})
add('codex',2,'sam@example.org',[window('codex/5h',88,300),window('codex/weekly',91,10080)],credits={'display':'560 créditos'})
add('claude',1,'alex@example.org',[window('Current session',50,300),window('Current week (all models)',67,10080),window('Current week (Fable)',97,10080)])
add('claude',2,'dani@example.org',[window('Current session',86,300),window('Current week (all models)',0,10080),window('Current week (Fable)',77,10080)])
add('agy','current',None,[window('Gemini Models · 5h',100,300),window('Gemini Models · Semanal',95,10080),window('Claude and GPT models · 5h',80,300),window('Claude and GPT models · Semanal',70,10080)],
 models=[{'name':'Gemini 3.8 Flash','ids':['gemini-3.8-flash']},{'name':'Gemini 3.1 Pro','ids':['gemini-3.1-pro']},{'name':'Claude Opus 5.5','ids':['claude-opus-5-5']},{'name':'Claude Sonnet 5.5','ids':['claude-sonnet-5-5']}],credits={'display':'0 créditos'})
add('opencode','current',None,[],status='UNKNOWN',credits=None,activity={'windows':[
 {'name':'5h','tokens':{'total':120000},'load_percent':24},{'name':'7d','tokens':{'total':920000},'load_percent':46}],
 'errors_429':1,'errors':{'429':1}},free_models=[{'id':'demo/free','name':'Modelo FREE (ejemplo)'}])
output=io.StringIO()
with patch.dict(os.environ,{'AI_MANAGER_COLOR':'always'}),patch('ai_manager.ui.shutil.get_terminal_size',return_value=os.terminal_size((174,50))),contextlib.redirect_stdout(output):
 render_usage(accounts,rows,lambda w:'hoy 16:30' if w.get('window_minutes')==300 else 'mar 13 · 19:00',lambda r:0)
lines=['$ ai usage','',*output.getvalue().splitlines()]
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',14)
cell=font.getlength('M');lineheight=23;padding=30
width=int(174*cell+2*padding);height=92+lineheight*len(lines)+45
im=Image.new('RGB',(width,height),'#0b111b');draw=ImageDraw.Draw(im)
draw.rectangle((0,0,width,58),fill='#162130')
for x,c in zip((30,53,76),('#ff6b6b','#f5c451','#5dd39e')):draw.ellipse((x,22,x+11,33),fill=c)
draw.text((width/2-140,19),'AI COMMAND  /  QUOTAS',font=font,fill='#94a8bd')
palette={30:'#c2cedc',31:'#ff6b76',32:'#67d7a0',33:'#f7ce6c',34:'#7fb8ff',35:'#d09af7',36:'#64d9e4',37:'#e5ecf3',90:'#91a1b4',96:'#64d9e4',97:'#f5f7fa'}
ansi=re.compile(r'\x1b\[([0-9;]*)m')
for row,line in enumerate(lines):
 x=padding;y=83+row*lineheight;color='#d5deeb';last=0
 for match in ansi.finditer(line):
  text=line[last:match.start()];draw.text((x,y),text,font=font,fill=color);x+=font.getlength(text)
  codes=[int(v or 0) for v in match[1].split(';')]
  if 0 in codes:color='#d5deeb'
  for code in codes:
   if code in palette:color=palette[code]
  last=match.end()
 draw.text((x,y),line[last:],font=font,fill=color)
draw.text((padding,height-27),'DEMO · Datos ficticios · Ninguna cuenta real',font=font,fill='#91a1b4')
target=ROOT/'docs/images/usage.png';target.parent.mkdir(parents=True,exist_ok=True);im.save(target,optimize=True)
print(target.relative_to(ROOT))
