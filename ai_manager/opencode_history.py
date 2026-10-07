"""Local token activity and error benchmarks, never invented provider quotas.

Read only selected metadata columns. No prompts, responses, paths, error text,
response bodies, authentication, or transcripts are returned or cached.
"""
from bisect import bisect_right
from collections import Counter, defaultdict
import contextlib
import datetime as dt
import math
from pathlib import Path
import re
import sqlite3
import statistics
import time

from .core import ManagerError


FIELDS=('input','output','reasoning','cache_read','cache_write')
WINDOWS=(('5h',5*3600),('7d',7*86400))


def numeric(value):
    return int(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else 0


def iso(stamp):
    return dt.datetime.fromtimestamp(stamp,dt.timezone.utc).isoformat(timespec='seconds')


class Series:
    def __init__(self,records):
        self.records=sorted(records,key=lambda r:r['at'])
        self.times=[r['at'] for r in self.records]
        self.prefix={key:[0] for key in (*FIELDS,'total')}
        for row in self.records:
            for key in self.prefix:self.prefix[key].append(self.prefix[key][-1]+row[key])

    def window(self,at,seconds):
        left=bisect_right(self.times,at-seconds);right=bisect_right(self.times,at)
        return {key:values[right]-values[left] for key,values in self.prefix.items()}

    def peak(self,seconds):
        peak=0;left=0;prefix=self.prefix['total']
        for right,row in enumerate(self.records):
            while self.records[left]['at']<=row['at']-seconds:left+=1
            peak=max(peak,prefix[right+1]-prefix[left])
        return peak


def summarize(records,at):
    records=[r for r in records if r['at']<=at]
    series=Series(records);groups=defaultdict(list)
    for row in records:groups[(row['provider'],row['model'])].append(row)
    errors=Counter(str(r['status']) if r['status'] else r['error'] for r in records if r['error'])
    result={'source':'OpenCode local SQLite metadata (read-only)','queried_at':iso(at),
            'record_count':len(records),'last_activity_at':iso(max(r['at'] for r in records)) if records else None,
            'errors':dict(errors),'errors_429':errors.get('429',0),
            'quota_available_percent':None,'confidence':'activity only; quota UNKNOWN',
            'note':'Las barras comparan actividad local con picos históricos; no representan cuota restante.',
            'windows':[],'models':[]}
    for name,seconds in WINDOWS:
        current=series.window(at,seconds);peak=series.peak(seconds)
        result['windows'].append({'name':name,'seconds':seconds,'tokens':current,
                                  'historical_peak_tokens':peak,
                                  'load_percent':round(100*current['total']/peak,3) if peak else None,
                                  'reference_kind':'observed local peak',
                                  'errors_429':sum(r['status']==429 and at-seconds<r['at']<=at for r in records)})
    for (provider,model),rows in sorted(groups.items()):
        group=Series(rows);rate_errors=[r for r in rows if r['status']==429]
        # Repeated retries in one burst are not independent saturation samples.
        samples=[];previous=None
        for row in rate_errors:
            if previous is None or row['at']-previous>=1800:samples.append(row)
            previous=row['at']
        entry={'provider':provider,'model':model,'errors_429':len(rate_errors),
               'other_errors':sum(bool(r['error']) and r['status']!=429 for r in rows),
               'independent_429_bursts':len(samples),'windows':[]}
        for name,seconds in WINDOWS:
            before=[group.window(r['at'],seconds)['total'] for r in samples]
            usable=[value for value in before if value>0]
            reference=int(statistics.median(usable)) if usable else None
            current=group.window(at,seconds)
            entry['windows'].append({'name':name,'tokens':current,'tokens_before_429':before,
                                     'median_tokens_before_429':reference,
                                     'relative_to_429_percent':round(100*current['total']/reference,3) if reference else None,
                                     'available_percent':None,'reset_at':None,
                                     'confidence':'LOW: 429 does not identify a token budget or a reset window'})
        result['models'].append(entry)
    return result


def read_history(path,at=None):
    path=Path(path)
    if not path.is_file():raise ManagerError('OpenCode: no hay base local de actividad')
    # Components match the installed official `stats`: input is non-cache,
    # output excludes reasoning, so add each component exactly once. Never add
    # tokens.total again, and never count step-finish parts a second time.
    query="""select
        coalesce(json_extract(data,'$.time.completed'),json_extract(data,'$.time.created'),time_created),
        json_extract(data,'$.providerID'),json_extract(data,'$.modelID'),
        json_extract(data,'$.tokens.input'),json_extract(data,'$.tokens.output'),
        json_extract(data,'$.tokens.reasoning'),json_extract(data,'$.tokens.cache.read'),
        json_extract(data,'$.tokens.cache.write'),json_extract(data,'$.error.name'),
        json_extract(data,'$.error.data.statusCode')
        from message where json_valid(data) and json_extract(data,'$.role')='assistant'
        order by 1"""
    records=[]
    try:
        with contextlib.closing(sqlite3.connect(path.absolute().as_uri()+'?mode=ro',uri=True,timeout=2)) as conn:
            conn.execute('pragma query_only=ON')
            columns={row[1] for row in conn.execute('pragma table_info(message)')}
            if not {'data','time_created'}<=columns:raise ManagerError('OpenCode: esquema de mensajes no reconocido')
            deadline=time.monotonic()+8
            conn.set_progress_handler(lambda:time.monotonic()>deadline,10000)
            for stamp,provider,model,*metadata in conn.execute(query):
                if not isinstance(stamp,(int,float)) or not 0<stamp<1e15:continue
                if not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_./:-]{1,160}',v) for v in (provider,model)):continue
                tokens=dict(zip(FIELDS,map(numeric,metadata[:5])))
                error=metadata[5];status=metadata[6]
                if not isinstance(error,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,64}',error):error='OtherError' if error else None
                if isinstance(status,str) and status.isdigit():status=int(status)
                status=status if isinstance(status,int) and 100<=status<=599 else None
                records.append(dict(at=stamp/1000,provider=provider,model=model,error=error,status=status,
                                    total=sum(tokens.values()),**tokens))
    except sqlite3.Error:
        raise ManagerError('OpenCode: lectura local no disponible o esquema modificado') from None
    return summarize(records,time.time() if at is None else at)
