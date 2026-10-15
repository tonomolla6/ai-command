import datetime as dt
import os
import unittest
from unittest.mock import patch

from ai_manager.cli import parser
from ai_manager.ui import usage_lines, visible_width
from ai_manager.compact_usage import compact_account


NOW=dt.datetime(2026,10,15,10,tzinfo=dt.timezone.utc)


def fixtures():
    accounts=[];rows=[]
    for provider in ('codex','claude'):
        for number in ('1','2','4','6'):
            a={'provider':provider,'account':number,'label':provider+' '+number,
               'email':provider+number+'@example.invalid'}
            windows=[{'name':'Current session' if provider=='claude' else 'codex/5h',
                      'available_percent':75,'window_minutes':300,'reset_at':(NOW+dt.timedelta(hours=1)).isoformat()},
                     {'name':'Current week (all models)' if provider=='claude' else 'codex/weekly',
                      'available_percent':0,'window_minutes':10080,'reset_at':(NOW+dt.timedelta(days=2)).isoformat()}]
            if provider=='claude':windows.append(dict(windows[-1],name='Current week (Fable)',available_percent=94))
            row={'provider':provider,'account':number,'email':a['email'],'email_verified':True,
                 'status':'OK','queried_at':NOW.isoformat(),'windows':windows,
                 'credits':{'display':'Desactivados'},'reset_credits_available':2}
            accounts.append(a);rows.append(row)
    accounts.extend([{'provider':p,'account':'current','label':p,'single':True} for p in ('agy','opencode')])
    rows.extend([{'provider':'agy','account':'current','status':'OK','queried_at':NOW.isoformat(),
                  'windows':[{'name':family+' · '+window,'available_percent':0,'reset_at':(NOW+dt.timedelta(hours=hours)).isoformat()}
                             for family in ('Gemini Models','Claude and GPT models') for window,hours in [('5h',1),('Semanal',48)]],
                  'models':[{'name':'Gemini 3.8 Flash','ids':['gemini-flash']},
                            {'name':'Claude Opus 5.5','ids':['claude-opus']},
                            {'name':'Claude Sonnet 5.5','ids':['claude-sonnet']}],
                  'credits':{'display':'Desactivados'}},
                 {'provider':'opencode','account':'current','status':'UNKNOWN','queried_at':NOW.isoformat(),
                  'windows':[],'free_models':[{'id':str(i),'name':'DemoFREE-'+str(i)} for i in range(120)],
                  'activity':{'windows':[{'name':w,'tokens':{'total':1000},'load_percent':20} for w in ('5h','7d')],
                              'errors_429':3,'errors':{'429':3}}}])
    return accounts,rows


class CompactUsageTests(unittest.TestCase):
    def render(self,compact=True,width=120,provider=None,color='never'):
        accounts,rows=fixtures()
        if provider:
            pairs=[(a,r) for a,r in zip(accounts,rows) if a['provider']==provider]
            accounts,rows=map(list,zip(*pairs))
        with patch('ai_manager.ui.shutil.get_terminal_size',return_value=os.terminal_size((width,24))), \
             patch.dict(os.environ,{'AI_MANAGER_COLOR':color}):
            return usage_lines(accounts,rows,lambda w:'17/10 12:00 CEST',lambda row:0,
                               compact=compact,reference=NOW)

    def test_four_accounts_each_provider_and_both_other_tools_fit_in_24_lines(self):
        lines=self.render()
        self.assertLessEqual(len(lines),22)
        text='\n'.join(lines)
        for provider in ('codex','claude'):
            for number in ('1','2','4','6'):self.assertIn(provider+number+'@example.invalid',text)
        for name in ('OPENAI','ANTHROPIC','AGY','OPENCODE','Fable','Cr','R 2','Próx. conocido'):
            self.assertIn(name,text)
        self.assertTrue(all(visible_width(line)<=120 for line in lines))
        self.assertNotIn('DemoFREE-0',text)
        self.assertIn('120 modelos FREE',text)
        self.assertIn('actividad local',text)
        self.assertIn('3.8 Flash',text);self.assertIn('Opus 5.5, Sonnet 5.5',text)

    def test_detailed_view_keeps_full_cards_and_catalog(self):
        text='\n'.join(self.render(compact=False))
        self.assertIn('DemoFREE-0',text);self.assertIn('Créditos:',text)
        self.assertGreater(len(text.splitlines()),24)

    def test_single_provider_uses_all_width_and_no_other_provider_is_displayed(self):
        lines=self.render(provider='claude',width=80)
        self.assertLessEqual(len(lines),24)
        text='\n'.join(lines)
        self.assertNotIn('OPENAI',text);self.assertNotIn('AGY',text)
        self.assertTrue(all(visible_width(line)<=80 for line in lines))

    def test_colorless_and_colored_layout_both_explain_blocked_positive_windows(self):
        for color in ('always','never'):
            with self.subTest(color=color):
                lines=self.render(provider='claude',width=80,color=color);text='\n'.join(lines)
                self.assertIn('75%!',text);self.assertIn('0%',text)
                self.assertEqual('\x1b[' in text,color=='always')
                if color=='always':self.assertIn('\x1b[31m',text)

    def test_detailed_flag_defaults_to_compact_and_works_with_monitoring(self):
        self.assertFalse(parser().parse_args(['usage']).detailed)
        self.assertTrue(parser().parse_args(['usage','--detailed']).detailed)
        self.assertTrue(parser().parse_args(['usage','claude','--monitoring','--detailed']).detailed)
        self.assertTrue(parser().parse_args(['limits','--detailed']).detailed)

    def test_compact_reset_balances_distinguish_zero_unpublished_and_invalid_data(self):
        a,r=fixtures();a,r=a[0],r[0]
        for value,expected in ((0,'R 0'),(1,'R 1'),(None,'R —'),(True,'R ?'),(-1,'R ?')):
            with self.subTest(value=value),patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
                text='\n'.join(compact_account(a,dict(r,reset_credits_available=value),80,NOW,lambda r:0))
            self.assertIn(expected,text)

    def test_fixed_models_only_say_manual_when_the_account_is_reserved(self):
        a,r=fixtures();a,r=a[0],r[0]
        for automatic in (False,True):
            with patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
                text='\n'.join(compact_account(dict(a,automatic=automatic,fixed_model='example-model'),r,80,NOW,lambda r:0))
            self.assertEqual('[M]' in text,not automatic)
            self.assertEqual('manual' in text,not automatic)
