import contextlib
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ai_manager.core import ManagerError
from ai_manager.opencode_history import read_history, summarize


class LocalHistoryTests(unittest.TestCase):
    now=2_000_000

    def event(self,hours,tokens=0,provider='opencode',model='model-a',status=None):
        return dict(at=self.now-hours*3600,provider=provider,model=model,
                    error='APIError' if status else None,status=status,total=tokens,
                    input=tokens,output=0,reasoning=0,cache_read=0,cache_write=0)

    def test_rolling_windows_exclude_boundary_and_future_and_keep_provider_model_scopes(self):
        rows=[self.event(6,100),self.event(5,999),self.event(4,1000),self.event(1,500),
              self.event(2,2000,provider='nvidia',model='other'),self.event(-1,999999)]
        result=summarize(rows,self.now)
        self.assertEqual([w['tokens']['total'] for w in result['windows']],[3500,4599])
        self.assertIsNone(result['quota_available_percent'])
        first=next(m for m in result['models'] if m['provider']=='opencode')
        self.assertEqual(first['windows'][0]['tokens']['total'],1500)
        self.assertIsNone(first['windows'][0]['median_tokens_before_429'])
        self.assertTrue(all(w['available_percent'] is None for m in result['models'] for w in m['windows']))

    def test_429_benchmarks_deduplicate_retries_and_never_use_other_error_types_as_quota(self):
        rows=[self.event(4.1,1000),self.event(4,status=429),self.event(3.999,status=429),
              self.event(1,500),self.event(.5,status=503),self.event(.25,status=401),
              self.event(4,99999,provider='other',model='model-a')]
        result=summarize(rows,self.now)
        first=next(m for m in result['models'] if m['provider']=='opencode')
        self.assertEqual(result['errors'],{'429':2,'503':1,'401':1})
        self.assertEqual(first['independent_429_bursts'],1)
        self.assertEqual(first['windows'][0]['tokens_before_429'],[1000])
        self.assertEqual(first['windows'][0]['median_tokens_before_429'],1000)
        self.assertEqual(first['windows'][0]['relative_to_429_percent'],150)
        self.assertIsNone(first['windows'][0]['available_percent'])
        self.assertIsNone(first['windows'][0]['reset_at'])
        self.assertIn('LOW',first['windows'][0]['confidence'])

    def test_read_only_metadata_counts_components_once_without_caching_private_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'history.db'
            with contextlib.closing(sqlite3.connect(path)) as conn, conn:
                conn.execute('create table message (id text,data text,time_created integer)')
                data={'role':'assistant','providerID':'opencode','modelID':'model-a',
                      'time':{'created':(self.now-100)*1000,'completed':(self.now-50)*1000},
                      'tokens':{'total':1375,'input':1000,'output':100,'reasoning':50,'cache':{'read':200,'write':25}},
                      'private_prompt':'FAKE_PRIVATE_TRANSCRIPT_VALUE',
                      'error':{'name':'APIError','data':{'statusCode':429,'message':'FAKE_PRIVATE_ERROR_VALUE'}}}
                conn.execute('insert into message values (?,?,?)',('one',json.dumps(data),(self.now-100)*1000))
                conn.execute('insert into message values (?,?,?)',('user',json.dumps(dict(data,role='user')),(self.now-100)*1000))
                conn.execute('insert into message values (?,?,?)',('invalid','{broken',(self.now-100)*1000))
                # step-finish includes the same tokens; message and part must not
                # both be counted as separate usage.
                conn.execute('create table part (data text)')
                conn.execute('insert into part values (?)',(json.dumps({'type':'step-finish','tokens':data['tokens']}),))
            before=hashlib.sha256(path.read_bytes()).digest()
            result=read_history(path,self.now)
            self.assertEqual(result['record_count'],1)
            self.assertEqual(result['windows'][0]['tokens']['total'],1375)
            self.assertEqual(result['windows'][0]['tokens']['cache_read'],200)
            self.assertNotIn('FAKE_PRIVATE',json.dumps(result))
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)

    def test_no_history_is_no_reference_not_full_quota(self):
        result=summarize([],self.now)
        self.assertTrue(all(w['tokens']['total']==0 and w['load_percent'] is None for w in result['windows']))
        self.assertIsNone(result['quota_available_percent'])

    def test_unknown_database_schema_is_reported_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'unknown.db'
            with contextlib.closing(sqlite3.connect(path)) as conn, conn:conn.execute('create table changed_schema (text text)')
            before=path.read_bytes()
            with self.assertRaises(ManagerError):read_history(path,self.now)
            self.assertEqual(path.read_bytes(),before)
