"""Seven-day diagnostics retention and cumulative student success ranking.

The XLSX layout is an embedded Artifact Tool template. Runtime fills its
OOXML cells with standard-library code, so recipients need no authoring runtime.
"""
import base64
import copy
from contextlib import closing
import fnmatch
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from rank_template import TEMPLATE_XLSX

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('x',NS)
Q=lambda tag:'{'+NS+'}'+tag
RANK_NAME='学生补录次数排行榜.xlsx'
LOG_PATTERNS=('运行日志-*.txt','错误详情*.txt','异常-*.png','保存返回-*.html',
              '预览-*.csv','扫描进度-*.csv','执行结果-*.csv','执行进度-*.csv','扫描中断已核对记录-*.csv')


def cleanup_logs(root,now=None,days=7):
    root=Path(root).resolve()
    if not root.is_dir(): return []
    cutoff=(time.time() if now is None else now)-days*86400
    removed=[]
    for p in root.iterdir():
        if not any(fnmatch.fnmatchcase(p.name,pattern) for pattern in LOG_PATTERNS):continue
        try:
            st=p.lstat()
            if p.is_symlink() or getattr(st,'st_file_attributes',0)&0x400:continue
            if not p.is_file() or p.resolve().parent!=root or st.st_mtime>=cutoff:continue
            p.unlink();removed.append(p.name)
        except OSError:
            continue  # A log currently held open can be removed on the next check.
    return removed


def _dt(value):
    value=' '.join(str(value).split())
    result=datetime.fromisoformat(value)
    return result.astimezone().replace(tzinfo=None) if result.tzinfo else result


def success_rows(db_path):
    db_path=Path(db_path)
    if not db_path.exists():return []
    with closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute("SELECT record_id,application_id,plan,updated FROM actions WHERE state='done' ORDER BY updated,record_id").fetchall()
    by_application={}
    for row in rows:
        if not row['application_id']:continue
        try:
            p=json.loads(row['plan'])
            sid=str(p['studentId']).strip();name=str(p['username']).strip()
            if not sid or not name:raise ValueError('缺少学号或姓名')
            item=dict(record_id=row['record_id'],application_id=row['application_id'],studentId=sid,username=name,
                      departName=p.get('departName',''),specialtyName=p.get('original_specialtyName',p.get('specialtyName','')),
                      naturalclassName=p.get('original_naturalclassName',p.get('naturalclassName','')),grade=p.get('grade',''),
                      lateDate=_dt(p['lateDate']),beginDate=_dt(p['beginDate']),endDate=_dt(p['endDate']),
                      confirmed=_dt(row['updated']),fallback='是' if p.get('fallback_reason') else '否')
        except (KeyError,TypeError,ValueError) as exc:
            raise ValueError('成功流水资料不完整，保留旧排行榜，记录ID：'+row['record_id']) from exc
        old=by_application.get(item['application_id'])
        if old and old['studentId']!=sid:
            raise ValueError('同一申请ID对应不同学号，停止更新排行榜')
        by_application[item['application_id']]=item
    return sorted(by_application.values(),key=lambda r:(r['confirmed'],r['record_id']),reverse=True)


def ranking_rows(details):
    groups={}
    for d in sorted(details,key=lambda r:(r['confirmed'],r['record_id'])):
        old=groups.get(d['studentId'])
        latest=dict(d,count=1 if old is None else old['count']+1,
                    last_late=d['lateDate'] if old is None else max(old['last_late'],d['lateDate']))
        groups[d['studentId']]=latest
    ordered=sorted(groups.values(),key=lambda r:(-r['count'],r['studentId']))
    previous=None;rank=0
    for i,row in enumerate(ordered,1):
        if row['count']!=previous:rank=i
        row['rank']=rank;previous=row['count']
    return ordered


def _cell(ref,value,style=None):
    cell=ET.Element(Q('c'),r=ref)
    if style is not None:cell.set('s',style)
    if isinstance(value,datetime):
        value=(value-datetime(1899,12,30)).total_seconds()/86400
    if isinstance(value,(int,float)):
        ET.SubElement(cell,Q('v')).text=str(value)
    else:
        cell.set('t','inlineStr')
        text=ET.SubElement(ET.SubElement(cell,Q('is')),Q('t'))
        text.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
        text.text='' if value is None else str(value)
    return cell


def _sheet(xml,rows,last_col,metadata):
    source=xml.decode('utf-8');root=ET.fromstring(source);old=root.find(Q('sheetData'))
    prototypes=[]
    for num in (7,8):
        r=old.find(f"{Q('row')}[@r='{num}']")
        prototypes.append({re.sub(r'\d+','',c.get('r')):c.get('s') for c in r.findall(Q('c'))})
    data=ET.Element(Q('sheetData'))
    for row in old.findall(Q('row')):
        if int(row.get('r'))<=6:data.append(copy.deepcopy(row))
    meta=data.find(f"{Q('row')}[@r='4']")
    old_meta=meta.find(f"{Q('c')}[@r='A4']")
    style=old_meta.get('s') if old_meta is not None else None
    if old_meta is not None:meta.remove(old_meta)
    meta.insert(0,_cell('A4',metadata,style))
    if not rows:rows=[['尚无已核实成功记录']]
    for offset,values in enumerate(rows):
        number=7+offset
        row=ET.SubElement(data,Q('row'),r=str(number),ht='24',customHeight='1')
        for index,value in enumerate(values):
            col=chr(65+index)
            row.append(_cell(col+str(number),value,prototypes[offset%2].get(col)))
    fragment=ET.tostring(data,encoding='unicode')
    pattern=r'<((?:[A-Za-z_][\w.-]*:)?sheetData)\b[^>]*>[\s\S]*?</\1>'
    assert re.search(pattern,source),'缺少模板sheetData'
    source=re.sub(pattern,lambda _:fragment,source,count=1)
    # Keep the original worksheet envelope and its namespace declarations intact.
    ref='A6:'+last_col+str(6+len(rows))
    if re.search(r'<(?:\w+:)?autoFilter\b',source):
        source=re.sub(r'(<(?:\w+:)?autoFilter\b[^>]*\bref=")[^"]*',lambda m:m[1]+ref,source,count=1)
    else:
        source=source.replace(fragment,fragment+'<x:autoFilter ref="'+ref+'" />',1)
    source=re.sub(r'(<(?:\w+:)?dimension\b[^>]*\bref=")[^"]*',lambda m:m[1]+'A1:'+last_col+str(6+len(rows)),source,count=1)
    return source.encode('utf-8')


def export_ranking(db_path,target):
    details=success_rows(db_path);ranks=ranking_rows(details)
    meta=f'更新：{datetime.now():%Y-%m-%d %H:%M:%S}    累计成功补录：{len(details)}次    学生：{len(ranks)}人'
    rank_values=[[r['rank'],r['studentId'],r['username'],r['departName'],r['specialtyName'],r['naturalclassName'],r['grade'],r['count'],r['last_late'],r['confirmed']] for r in ranks]
    detail_values=[[d['studentId'],d['username'],d['departName'],d['specialtyName'],d['naturalclassName'],d['grade'],d['lateDate'],d['beginDate'],d['endDate'],d['application_id'],d['confirmed'],d['record_id'],d['fallback']] for d in details]
    changes={}
    template=zipfile.ZipFile(io.BytesIO(base64.b64decode(TEMPLATE_XLSX)))
    changes['xl/worksheets/sheet1.xml']=_sheet(template.read('xl/worksheets/sheet1.xml'),rank_values,'J',meta)
    changes['xl/worksheets/sheet2.xml']=_sheet(template.read('xl/worksheets/sheet2.xml'),detail_values,'M',meta)
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='排行榜-',suffix='.tmp',dir=target.parent);os.close(fd)
    try:
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as out:
            for item in template.infolist():out.writestr(item,changes.get(item.filename,template.read(item.filename)))
        template.close()
        os.replace(temp,target)
    finally:
        template.close()
        if os.path.exists(temp):os.unlink(temp)
    return target


class Maintenance:
    def __init__(self,root,log=lambda message:None,clock=time.time):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.log=log;self.clock=clock;self.last_cleanup=None;self.last_export=None;self.retry_after=0;self.last_error=None;self.export_pending=False
        self.path=self.root/RANK_NAME
    def run(self,force=False,force_export=False):
        now=self.clock()
        if force or self.last_cleanup is None or now<self.last_cleanup or now-self.last_cleanup>=86400:
            legacy=self.root/'错误详情.txt'
            try:
                if legacy.is_file() and not legacy.is_symlink() and not legacy.read_text(encoding='utf-8',errors='replace').startswith('最近错误（'):
                    backup=self.root/f'错误详情-历史记录-{legacy.stat().st_mtime_ns}.txt'
                    if not backup.exists():shutil.copy2(legacy,backup)
            except OSError:pass
            deleted=cleanup_logs(self.root,now=now)
            self.last_cleanup=now
            if deleted:self.log('自动清理超过7天的诊断文件：'+str(len(deleted))+'个；永久流水和排行榜保留。')
        due=self.export_pending or self.last_export is None or now<self.last_export or now-self.last_export>=3600
        if (force or force_export or due) and (force or now>=self.retry_after):
            try:
                export_ranking(self.root/'journal.db',self.path)
                self.last_export=now;self.retry_after=0;self.last_error=None;self.export_pending=False
            except Exception as exc:
                self.retry_after=now+300;self.last_error=str(exc);self.export_pending=True
                self.log('排行榜暂未更新，成功流水已保留；如Excel已打开，请关闭后点击“学生排行榜”刷新。原因：'+str(exc))
        return self.path if self.path.exists() else None
