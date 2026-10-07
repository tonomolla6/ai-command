import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.cli import limits, parser
from ai_manager.core import Manager


class UsageFilterTests(unittest.TestCase):
    def test_filter_queries_and_outputs_only_selected_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=Manager(tmp);manager.state.mkdir(parents=True)
            manager.config={'schema':1,'accounts':[{'provider':p,'account':n,'home':tmp,'label':p+' '+n}
                                for p in ('codex','claude') for n in ('2','4')]}
            singles=[{'provider':p,'account':'current','single':True,'label':p,'home':tmp} for p in ('agy','opencode')]
            for provider in ('codex','claude','agy','opencode'):
                args=parser().parse_args(['usage',provider,'--refresh','--json'])
                def query(m,accounts):
                    self.assertTrue(all(a['provider']==provider for a in accounts))
                    return {m.key(a):{'provider':a['provider'],'account':a['account'],'status':'UNKNOWN','windows':[]} for a in accounts}
                out=io.StringIO()
                with patch('ai_manager.cli.current_tools',return_value=singles), \
                     patch('ai_manager.cli.collect_limits',side_effect=query) as collect,contextlib.redirect_stdout(out):
                    limits(manager,args)
                collect.assert_called_once()
                rows=json.loads(out.getvalue())['accounts']
                self.assertEqual(len(rows),2 if provider in ('codex','claude') else 1)
                self.assertTrue(all(row['provider']==provider for row in rows))
                self.assertEqual(parser().parse_args(['limits',provider,'--cached']).provider,provider)

    def test_filtered_layout_uses_full_width_instead_of_empty_provider_columns(self):
        from ai_manager.ui import render_usage
        out=io.StringIO()
        with patch('ai_manager.ui.shutil.get_terminal_size',return_value=__import__('os').terminal_size((180,24))), \
             contextlib.redirect_stdout(out):
            render_usage([{'provider':'agy','account':'current','single':True,'label':'AGY'}],
                         [{'status':'UNKNOWN','windows':[]}],lambda w:'UNKNOWN',lambda r:0)
        header=out.getvalue().splitlines()[1]
        self.assertTrue(header.startswith('AGY'))
        self.assertNotIn('OPENAI',out.getvalue());self.assertNotIn('ANTHROPIC',out.getvalue())
