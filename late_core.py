"""Pure parsing, date rules, and durable write journal."""
import json
import re
import sqlite3
from datetime import datetime, timedelta, date
from bs4 import BeautifulSoup

FMT = '%Y-%m-%d %H:%M:%S'


def stamp(value):
    value = ' '.join(value.split())
    return datetime.strptime(value, FMT if len(value) > 16 else '%Y-%m-%d %H:%M')


def interval(late, minimum=None, maximum=None):
    t = stamp(late)
    lo, hi = stamp(minimum) if minimum else None, stamp(maximum) if maximum else None
    if (lo and t < lo) or (hi and t > hi) or (lo and hi and lo > hi):
        raise ValueError('晚归时间不在页面允许的学期范围内')
    start, end = t - timedelta(minutes=30), t + timedelta(minutes=30)
    if lo and start < lo:
        start, end = lo, lo + timedelta(hours=1)
    if hi and end > hi:
        start, end = hi - timedelta(hours=1), hi
    if lo:
        start = max(start, lo)
    if not start <= t <= end or not timedelta(0) < end-start <= timedelta(hours=1):
        raise ValueError('无法构造包含晚归时间的一小时内区间')
    return start.strftime(FMT), end.strftime(FMT)


def validate_interval(late, begin, end, minimum=None, maximum=None):
    t,start,finish=stamp(late),stamp(begin),stamp(end)
    if not start<=t<=finish or not timedelta(0)<finish-start<=timedelta(hours=1):
        raise ValueError('申请区间必须包含晚归时间，且时长大于0、不超过一小时')
    if (minimum and start<stamp(minimum)) or (maximum and finish>stamp(maximum)):
        raise ValueError('申请区间超出页面允许的学期范围')


def overlapping(apps,plan):
    start,finish=stamp(plan['beginDate']),stamp(plan['endDate'])
    return [a for a in apps if a['studentId']==plan['studentId'] and a['status'] in ('待审核','审核通过')
            and stamp(a['beginDate'])<=finish and stamp(a['endDate'])>=start]


def conflict_free_interval(late,apps,student_id,minimum=None,maximum=None):
    start,finish=map(stamp,interval(late,minimum,maximum))
    t=stamp(late)
    lower=stamp(minimum) if minimum else None
    upper=stamp(maximum) if maximum else None
    for a in apps:
        if a['studentId']!=student_id or a['status'] not in ('待审核','审核通过'):
            continue
        astart,aend=stamp(a['beginDate']),stamp(a['endDate'])
        if astart>aend:
            raise ValueError('已有申请起止时间异常，无法判断冲突')
        if astart<=t<=aend:
            raise ValueError('已有有效申请覆盖晚归时间，不应新建补录')
        if aend<t:
            bound=aend+timedelta(seconds=1)
            lower=max(lower,bound) if lower else bound
        if astart>t:
            bound=astart-timedelta(seconds=1)
            upper=min(upper,bound) if upper else bound
    if lower: start=max(start,lower)
    finish=start+timedelta(hours=1)
    if upper and finish>upper:
        finish=upper
        start=finish-timedelta(hours=1)
        if lower: start=max(start,lower)
    begin,end=start.strftime(FMT),finish.strftime(FMT)
    validate_interval(late,begin,end,minimum,maximum)
    return begin,end


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split()) if node else ''


def rows(html):
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('#contentTable')
    if table is None:
        raise ValueError('未找到列表表格，可能登录已失效或页面结构改变')
    # The live site pads short pages with blank rows. They have no student text,
    # ID control or detail link, and are not records (including colspan padding).
    return [row for row in table.select('tbody > tr')
            if text(row) or row.select_one('input[name="ids"], a[href]')]


def parse_records(html, start, end):
    lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    if lo > hi:
        raise ValueError('开始日期不能晚于结束日期')
    result, seen = [], set()
    for row in rows(html):
        cells = row.find_all('td', recursive=False)
        if not cells:
            continue
        if len(cells) != 9:
            raise ValueError('违纪列表列数发生变化')
        values = [text(c) for c in cells]
        box, anchor = cells[0].select_one('input[name="ids"]'), cells[1].select_one('a[href]')
        if not box or not anchor:
            raise ValueError('违纪记录缺少 ID 或详情链接')
        rid = box.get('value', '')
        if not rid or rid in seen:
            continue
        if values[8] not in ('无申请','审核不通过') or values[7] != '进' or not lo <= stamp(values[6]).date() <= hi:
            continue
        seen.add(rid)
        result.append(dict(record_id=rid, source_status=values[8], studentId=values[1], username=values[2], departName=values[3], naturalclassName=values[4], grade=values[5], lateDate=values[6], detail_url=anchor['href']))
    return result


def option_index(options, value):
    """Same value and same label are equivalent; never infer grade from an ID."""
    matches=[(i,o) for i,o in enumerate(options) if o.get('value')==value]
    if not matches:
        raise ValueError('下拉列表中不存在目标选项：'+value)
    labels={' '.join(o.get('text','').split()) for _,o in matches}
    if len(labels)!=1:
        raise ValueError('同值选项的显示名称不同，无法安全选择：'+value)
    return matches[0][0]


def fallback_plan(plan, reason):
    result=dict(plan)
    result.update(original_specialtyName=plan['specialtyName'],
                  original_naturalclassName=plan['naturalclassName'],
                  specialtyName='机器人工程', naturalclassName='2025机器人1班',
                  fallback_reason=reason)
    return result


def fields(html):
    soup = BeautifulSoup(html, 'html.parser')
    result = {}
    for group in soup.select('#inputForm .control-group'):
        label, controls = group.select_one('label[for]'), group.select_one('.controls')
        if label and label.get('for'):
            result[label['for']] = text(controls)
    return result


def parse_detail(html, student_id):
    result = fields(html)
    for key in ('studentId','username','departName','specialtyName','naturalclassName','grade','lateDate','applyStatus','ioFlag'):
        if not result.get(key):
            raise ValueError('学生详情缺少字段：' + key)
    if result['studentId'] != student_id:
        raise ValueError('详情学号与列表学号不一致')
    return result


def parse_applications(html):
    result = []
    names = ('studentId','username','departName','naturalclassName','grade','beginDate','endDate','status','lateType','markup','createDate')
    for row in rows(html):
        cells = row.find_all('td', recursive=False)
        if not cells:
            continue
        if len(cells) != 13:
            raise ValueError('申请列表列数发生变化')
        box = cells[0].select_one('input[name="ids"]')
        if box is None:
            raise ValueError('申请缺少 ID')
        item = dict(zip(names, [text(c) for c in cells[1:12]]))
        item['id'] = box['value']
        link = cells[12].find('a', string=re.compile(r'^审核$'))
        item['audit_url'] = link.get('href') if link else None
        result.append(item)
    return result


def matching(apps, plan):
    return [a for a in apps if a['studentId'] == plan['studentId'] and a['username'] == plan['username']
            and stamp(a['beginDate']) == stamp(plan['beginDate']) and stamp(a['endDate']) == stamp(plan['endDate'])
            and a['lateType'] == '学习原因' and a['markup'] == '补录']


def covering(apps, plan):
    t = stamp(plan['lateDate'])
    return [a for a in apps if a['studentId'] == plan['studentId'] and a['status'] in ('待审核','审核通过') and stamp(a['beginDate']) <= t <= stamp(a['endDate'])]


def rejected_application(apps, plan):
    """Require a unique rejected request covering this return, never name-only."""
    t=stamp(plan['lateDate'])
    matches=[a for a in apps if a['status']=='审核不通过'
             and all(a.get(k)==plan.get(k) for k in ('studentId','username','departName'))
             and stamp(a['beginDate'])<=t<=stamp(a['endDate'])]
    if len(matches)!=1:
        raise ValueError('无法唯一确定该次晚归的被拒申请（找到'+str(len(matches))+'条）；请人工核对，不会删除')
    target=matches[0]
    expected=plan.get('rejected_application')
    if expected and any(target.get(k)!=expected.get(k) for k in
                        ('id','studentId','username','departName','status','beginDate','endDate')):
        raise ValueError('被拒申请与预览不一致，请重新扫描；不会删除')
    return target


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE IF NOT EXISTS actions (record_id TEXT PRIMARY KEY, state TEXT NOT NULL, application_id TEXT, plan TEXT NOT NULL, updated TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS rejected_deletions (application_id TEXT PRIMARY KEY, record_id TEXT NOT NULL UNIQUE)')
        self.db.commit()

    def rejected_owner(self, application_id):
        row=self.db.execute('SELECT record_id FROM rejected_deletions WHERE application_id=?',(application_id,)).fetchone()
        return row[0] if row else None

    def get(self, record_id):
        row = self.db.execute('SELECT * FROM actions WHERE record_id=?', (record_id,)).fetchone()
        return dict(row) if row else None

    def begin(self, record_id, plan, state="saving"):
        if state not in ("saving","deleting"):
            raise ValueError("非法初始流水状态")
        try:
            if state=='deleting':
                target=plan.get('rejected_application',{}).get('id')
                if not target:raise ValueError('缺少被拒申请ID，禁止删除')
                self.db.execute('INSERT INTO rejected_deletions VALUES (?,?)',(target,record_id))
            self.db.execute('INSERT INTO actions VALUES (?, ?, ?, ?, ?)', (record_id, state, None, json.dumps(plan,ensure_ascii=False), datetime.now().isoformat()))
            self.db.commit()
        except sqlite3.IntegrityError as e:
            self.db.rollback()
            raise ValueError('该记录或被拒申请已有操作流水，禁止重复删除或新建；请先核对日志和学校申请列表') from e

    def update_plan(self, record_id, plan):
        self.db.execute('UPDATE actions SET plan=?, updated=? WHERE record_id=?',
                        (json.dumps(plan,ensure_ascii=False),datetime.now().isoformat(),record_id))
        self.db.commit()

    def update(self, record_id, state, application_id=None):
        self.db.execute('UPDATE actions SET state=?, application_id=COALESCE(?, application_id), updated=? WHERE record_id=?', (state, application_id, datetime.now().isoformat(), record_id))
        self.db.commit()

    def close(self):
        self.db.close()
