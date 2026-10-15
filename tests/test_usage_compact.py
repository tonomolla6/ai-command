import datetime as dt
import os
import unittest
from unittest.mock import patch

from ai_manager.cli import parser
from ai_manager.ui import ANSI, availability_lines, usage_lines, visible_width
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
                 'credits':{'display':'Desactivados'},'reset_credits_available':2 if provider=='codex' else None}
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

    def test_compact_windows_have_bars_without_adding_rows(self):
        for width in (120,138,180):
            for color in ('always','never'):
                with self.subTest(width=width,color=color):
                    lines=self.render(width=width,color=color)
                    text=ANSI.sub('','\n'.join(lines))
                    self.assertRegex(text,r'5h [█░]{3,6}75%!')
                    self.assertRegex(text,r'7d ░{3,6}0%')
                    self.assertRegex(text,r'Fable [█░]{3,6}94%!')
                    self.assertLessEqual(len(lines),22)
                    self.assertTrue(all(visible_width(line)<=width for line in lines))

    def test_provider_headers_show_readable_recovery_or_reset_with_available_accounts(self):
        accounts,rows=fixtures()
        for provider in ('codex','claude','agy'):
            with self.subTest(provider=provider),patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
                own=[i for i,a in enumerate(accounts) if a['provider']==provider]
                windows=rows[own[0]]['windows']
                for w in (windows[:2] if provider=='agy' else windows):w['available_percent']=80
                lines=availability_lines(accounts,rows,provider,lambda w:'17/10 12:00 CEST',NOW)
                self.assertIn('Con cuota:',lines[0])
                self.assertIn('Próx.',lines[1])
                self.assertIn('~2d 00h 00m 00s',lines[1])
                for i in own:
                    for w in rows[i]['windows']:w['available_percent']=80
                lines=availability_lines(accounts,rows,provider,lambda w:'17/10 12:00 CEST',NOW)
                self.assertIn('Reset ',lines[1]);self.assertIn('~01h 00m 00s',lines[1])
                self.assertEqual(len(lines),2)

    def test_bar_fill_and_color_preserve_blocked_window_percentages(self):
        accounts,rows=fixtures();a,r=accounts[4],rows[4]
        r=dict(r,windows=[dict(w,available_percent=value) for w,value in zip(r['windows'],(0,50,100))])
        with patch.dict(os.environ,{'AI_MANAGER_COLOR':'always'}):
            text='\n'.join(compact_account(a,r,80,NOW,lambda r:0))
        for metric in ('5h ░░░░░░0%','7d ███░░░50%!','Fable ██████100%!'):
            self.assertIn('\x1b[31m'+metric+'\x1b[0m',text)

    def test_unknown_window_never_gets_an_empty_availability_bar(self):
        accounts,rows=fixtures();a,r=accounts[0],rows[0]
        for value in (None,True,float('nan'),float('inf')):
            with self.subTest(value=value),patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
                text='\n'.join(compact_account(a,dict(r,windows=[dict(r['windows'][0],available_percent=value)]),80,NOW,lambda r:0))
                self.assertIn('UNKNOWN',text)
                self.assertNotIn('░',text)

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

    def test_different_weekly_model_resets_and_stale_metadata_still_fit(self):
        accounts,rows=fixtures()
        for a,row in zip(accounts,rows):
            if a['provider']=='claude':row['windows'][-1]['reset_at']=(NOW+dt.timedelta(days=3)).isoformat()
        with patch('ai_manager.ui.shutil.get_terminal_size',return_value=os.terminal_size((120,24))), \
             patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
            lines=usage_lines(accounts,rows,lambda w:'17/10 12:00 CEST',lambda r:180,
                              compact=True,reference=NOW)
        text='\n'.join(lines)
        self.assertLessEqual(len(lines),22)
        self.assertIn('17/10',text);self.assertIn('18/10',text)
        self.assertIn('STALE',text);self.assertIn('Cr OFF',text)
