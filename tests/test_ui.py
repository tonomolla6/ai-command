import contextlib
import io
import os
import unittest
from unittest.mock import patch

from ai_manager.ui import render_usage, visible_width, fit, usage_card, blocked_windows


class UsageLayoutTests(unittest.TestCase):
    def test_missing_login_does_not_show_an_old_verified_identity_as_current(self):
        account={'provider':'claude','account':'2','label':'Claude 2',
                 'email':'test@example.invalid','email_verified':True}
        row={'status':'SIN LOGIN','email_verified':False,'reason':'SIN LOGIN'}
        with patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
            text='\n'.join(usage_card(account,row,64,lambda w:'UNKNOWN',lambda r:0))
        self.assertIn('test@example.invalid ○',text)
        self.assertNotIn('✓',text)

    def render(self,width,color='never'):
        accounts=[{'provider':p,'account':'4','label':p.title()+' 4','email':p+'4@example.invalid'}
                  for p in ('codex','claude')]
        rows=[{'status':'OK','email_verified':True,'windows':[{'name':'weekly','available_percent':61}],
               'credits':{'display':'Desactivados'},'queried_at':'2026-10-07T09:00:00+00:00'}]*2
        out=io.StringIO()
        with patch('ai_manager.ui.shutil.get_terminal_size',return_value=os.terminal_size((width,24))), \
             patch.dict(os.environ,{'AI_MANAGER_COLOR':color}),contextlib.redirect_stdout(out):
            render_usage(accounts,rows,lambda w:'14/10 05:29 CEST',lambda r:5)
        return out.getvalue().splitlines()

    def test_wide_terminal_has_ordered_columns_with_all_accounts_windows_and_credits(self):
        lines=self.render(120,'always')
        header=next(line for line in lines if 'ANTHROPIC' in line)
        self.assertIn('OPENAI',header)
        self.assertLess(header.index('OPENAI'),header.index('ANTHROPIC'))
        text='\n'.join(lines)
        for value in ('codex4@example.invalid','claude4@example.invalid','61% disponible','Créditos:','14/10 05:29 CEST'):
            self.assertIn(value,text)
        self.assertTrue(all(visible_width(line)<=120 for line in lines))
        self.assertIn('\x1b[',text)

    def test_narrow_terminal_stacks_blocks_and_plain_output_has_no_ansi(self):
        lines=self.render(80)
        self.assertFalse(any('ANTHROPIC' in line and 'OPENAI' in line for line in lines))
        self.assertTrue(all(visible_width(line)<=80 for line in lines))
        self.assertNotIn('\x1b[','\n'.join(lines))
        self.assertEqual(sum(line.startswith('┌') for line in lines),2)

    def test_colored_wide_characters_do_not_break_card_width(self):
        value=fit('\x1b[35m账户很长的名称\x1b[0m',9)
        self.assertEqual(visible_width(value),9)
        self.assertIn('…',value)
        self.assertIn('\x1b[0m',value)

    def test_three_columns_put_codex_then_claude_then_stacked_agy_and_opencode(self):
        accounts=[{'provider':p,'account':'current' if p in ('agy','opencode') else '2','label':p.title(),
                   'single':p in ('agy','opencode')} for p in ('codex','claude','agy','opencode')]
        rows=[{'status':'OK','windows':[{'name':'weekly','available_percent':50}]} for _ in accounts]
        rows[2]['windows']=[{'name':'Claude and GPT models · Semanal','available_percent':50,'window_minutes':10080}]
        rows[2]['models']=[{'name':'Claude Sonnet 5.5','ids':['claude-sonnet-5-5-high'],'efforts':['low','medium','high']}]
        out=io.StringIO()
        with patch('ai_manager.ui.shutil.get_terminal_size',return_value=os.terminal_size((120,24))), \
             patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}),contextlib.redirect_stdout(out):
            render_usage(accounts,rows,lambda w:'UNKNOWN',lambda r:0)
        lines=out.getvalue().splitlines();header=lines[1]
        self.assertLess(header.index('OPENAI'),header.index('ANTHROPIC'))
        self.assertLess(header.index('ANTHROPIC'),header.index('AGY'))
        opencode=next(line for line in lines if 'OPENCODE / FREE' in line)
        self.assertEqual(opencode.index('OPENCODE'),header.index('AGY'))
        self.assertIn('Sonnet 5.5',out.getvalue())
        self.assertNotIn('Claude Sonnet',out.getvalue())
        self.assertNotIn('Modelos disponibles',out.getvalue())
        self.assertNotIn('low / medium / high',out.getvalue())
        self.assertNotIn('Claude and GPT models',out.getvalue())
        self.assertIn('Semanal · compartido',out.getvalue())
        self.assertTrue(all(visible_width(line)<=120 for line in lines))

    def test_global_weekly_exhaustion_marks_other_windows_red_and_blocked(self):
        for provider,names in [('claude',('Current session','Current week (all models)','Current week (Fable)')),
                               ('codex',('codex/5h','codex/weekly','base_model_inference/weekly'))]:
            a={'provider':provider,'account':'2','label':provider+' 2','email':'test@example.invalid'}
            row={'status':'OK','windows':[{'name':name,'available_percent':value} for name,value in zip(names,(100,0,75))]}
            with self.subTest(provider=provider),patch.dict(os.environ,{'AI_MANAGER_COLOR':'always'}):
                text='\n'.join(usage_card(a,row,64,lambda w:'tomorrow',lambda r:0))
            self.assertIn('Bloqueada por:',text)
            self.assertIn('\x1b[31m100% · BLOQUEADO\x1b[0m',text)
            self.assertIn('\x1b[31m75% · BLOQUEADO\x1b[0m',text)
            self.assertIn('\x1b[31m'+'█'*12,text)
            self.assertNotIn('\x1b[32m100% disponible',text)
            self.assertEqual(row['windows'][0]['available_percent'],100)

    def test_agy_and_model_specific_claude_limits_do_not_block_independent_allowances(self):
        windows=[{'name':'Gemini Models · Semanal','available_percent':0},
                 {'name':'Gemini Models · 5h','available_percent':100},
                 {'name':'Claude and GPT models · 5h','available_percent':90}]
        self.assertEqual(blocked_windows('agy',windows),windows[:2])
        windows=[{'name':'Current week (all models)','available_percent':40},
                 {'name':'Current week (Fable)','available_percent':0}]
        self.assertEqual(blocked_windows('claude',windows),windows[1:])

    def test_opencode_history_bars_are_activity_and_never_remaining_quota(self):
        account={'provider':'opencode','account':'current','single':True,'label':'OpenCode'}
        row={'status':'UNKNOWN','windows':[],'activity':{'windows':[{'name':'5h','tokens':{'total':12000},'load_percent':25}],
                                                       'errors':{'429':2,'503':1},'errors_429':2}}
        with patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
            text='\n'.join(usage_card(account,row,70,lambda w:'UNKNOWN',lambda r:0))
        self.assertIn('5h: 12.0k tokens',text)
        self.assertIn('≈25% del pico local',text)
        self.assertIn('cuota UNKNOWN',text)
        self.assertNotIn('25% disponible',text)

    def test_manual_reset_balance_is_separate_from_exhausted_quota_and_money(self):
        account={'provider':'codex','account':'2','label':'Codex 2'}
        row={'status':'OK','windows':[{'name':'codex/weekly','available_percent':0}],
             'credits':[{'balance':'125','bucket':'codex'}],'reset_credits_available':2}
        with patch.dict(os.environ,{'AI_MANAGER_COLOR':'always'}):
            text='\n'.join(usage_card(account,row,38,lambda w:'mañana',lambda r:0))
        self.assertIn('AGOTADO',text)
        self.assertIn('\x1b[32m2 disponibles\x1b[0m',text)
        self.assertIn('125.00 créditos',text)
        self.assertTrue(all(visible_width(line)==38 for line in text.splitlines()))
        self.assertEqual(row['windows'][0]['available_percent'],0)

    def test_zero_and_single_resets_keep_their_exact_meaning(self):
        account={'provider':'codex','account':'2','label':'Codex 2'}
        for count,expected in ((0,'0 disponibles'),(1,'1 disponible')):
            with self.subTest(count=count),patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
                text='\n'.join(usage_card(account,{'status':'OK','reset_credits_available':count},64,
                                        lambda w:'UNKNOWN',lambda r:0))
            self.assertIn('Resets: '+expected,text)

    def test_unpublished_resets_never_infer_zero_or_use_credit_balance(self):
        for provider in ('codex','claude','agy','opencode'):
            account={'provider':provider,'account':'2','label':provider}
            for status,expected in (('OK','No publicado por CLI'),('UNKNOWN','UNKNOWN'),('SIN LOGIN','UNKNOWN')):
                with self.subTest(provider=provider,status=status),patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
                    text='\n'.join(usage_card(account,{'status':status,'credits':{'balance':10}},64,
                                            lambda w:'UNKNOWN',lambda r:0))
                self.assertIn('Resets: '+expected,text)
                self.assertNotIn('0 disponibles',text)
            for invalid in (True,-1,1.5,'secret-value'):
                with patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):
                    text='\n'.join(usage_card(account,{'status':'OK','reset_credits_available':invalid},64,
                                            lambda w:'UNKNOWN',lambda r:0))
                self.assertIn('Resets: UNKNOWN',text)
                self.assertNotIn('secret-value',text)


if __name__=='__main__':unittest.main()
