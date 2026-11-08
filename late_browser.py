"""Edge automation using observed school forms; all mutations use visible controls."""
import csv
import os
import json
import re
import threading
import time
from functools import wraps
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlencode

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout, Error as PlaywrightError
from late_core import interval, parse_records, parse_detail, parse_applications, matching, covering, fields, stamp, option_index, fallback_plan, conflict_free_interval, validate_interval, overlapping, rejected_application

ORIGIN = 'https://sa.dgut.edu.cn'
BASE = '/sahManage/a/apply/'


def navigate_login(page, url, log):
    """A network failure must not discard a successfully opened login window."""
    try:
        page.goto(url, wait_until='commit', timeout=15000)
        return True
    except PlaywrightError as exc:
        if page.is_closed():
            raise
        log('自动打开学校网址未完成：'+str(exc).splitlines()[0])
        log('浏览器已保留。请在此窗口地址栏输入 https://sa.dgut.edu.cn/sahManage/a ，登录后点击扫描预览。')
        return False


class Stopped(Exception):
    pass


def retry_read(operation, log, check, pause, attempts=3):
    """Retry reads only. Callers must never pass a save/approval operation."""
    for attempt in range(attempts):
        check()
        try:
            return operation()
        except PlaywrightError as exc:
            message=str(exc).lower()
            if attempt+1>=attempts or any(word in message for word in ('has been closed','target closed','browser closed')):
                raise
            check()
            log('只读页面暂时失败，准备第'+str(attempt+2)+'/'+str(attempts)+'次尝试：'+str(exc).splitlines()[0])
            pause(2*(attempt+1))
    raise RuntimeError('读取重试次数无效')


def read_step(function):
    @wraps(function)
    def wrapped(self,*args,**kwargs):
        return retry_read(lambda:function(self,*args,**kwargs),self.log,self.check,self.pause)
    return wrapped


def poll_added(read, old_ids, plan, log, check, pause, attempts=3):
    for attempt in range(attempts):
        check()
        added=[a for a in matching(read(),plan) if a['id'] not in old_ids]
        if len(added)==1 and added[0]['status']=='待审核':
            return added[0]
        if len(added)>1 or (added and added[0]['status']!='待审核'):
            raise ValueError('新增申请存在歧义或状态变化，停止并保留流水，请人工核查')
        if attempt+1<attempts:
            log('保存后列表暂未显示唯一新申请，仅重新读取列表，不重复保存。')
            pause(2*(attempt+1))
    raise ValueError('保存后多次查询仍未确认唯一新申请；请核查流水和学校页面，不会再次保存')


class SchoolBrowser:
    def __init__(self, data_dir, log=lambda message: None, stop=None, origin=ORIGIN, headless=False, maintenance=None):
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.log_callback = log
        self.stop = stop or threading.Event()
        self.origin = origin.rstrip('/')
        self.headless = headless
        self.pw = self.context = self.page = None
        self.application_url = None
        self.last_scan_plans = []
        self.owner = None
        from late_core import Journal
        self.journal = Journal(self.data/'journal.db')
        self.session_id = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.log_path = self.data / f'运行日志-{self.session_id}.txt'
        self._log_day=datetime.now().strftime('%Y%m%d')
        from late_maintenance import Maintenance
        self.maintenance=maintenance if maintenance is not None else Maintenance(self.data,self.log)

    def log(self, message):
        now=datetime.now()
        day=now.strftime('%Y%m%d')
        if day!=self._log_day:
            self.log_path=self.data/f'运行日志-{day}-{self.session_id}.txt'
            self._log_day=day
        line = f'{now:%H:%M:%S} {message}'
        with self.log_path.open('a', encoding='utf-8') as f:
            f.write(line+'\n')
        self.log_callback(line)

    def check(self):
        if self.owner is not None and threading.get_ident() != self.owner:
            raise RuntimeError('浏览器必须由创建它的工作线程操作')
        if self.stop.is_set():
            raise Stopped('已停止；已发出的保存或审核不会自动撤销，请核对操作流水')

    def pause(self, seconds):
        self.check()
        if self.stop.wait(seconds):
            self.check()

    def safe_url(self, link):
        url = urljoin(self.origin+'/', link)
        p, allowed = urlparse(url), urlparse(self.origin)
        if (p.scheme, p.netloc) != (allowed.scheme, allowed.netloc) or not p.path.startswith(BASE):
            raise ValueError('页面链接不属于允许的晚归模块')
        return url

    def open(self):
        self.owner = threading.get_ident()
        self.pw = sync_playwright().start()
        self.context = self.pw.chromium.launch_persistent_context(
            str(self.data/'edge-profile'), channel='msedge', headless=self.headless,
            viewport={'width':1360,'height':900}, accept_downloads=False, timeout=30000)
        self.context.set_default_timeout(20000)
        self.context.set_default_navigation_timeout(45000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.log('Edge 已连接，正在打开学校网址（最多等待15秒）…')
        navigate_login(self.page, self.origin+'/sahManage/a', self.log)
        self.log('浏览器已打开。请手动登录并进入晚归管理，再点击扫描预览。')

    def frame(self):
        self.check()
        if not self.page or self.page.is_closed():
            raise ValueError('浏览器已关闭，请点击程序中的“重启浏览器”')
        frame = self.page.frame(name='mainFrame')
        if frame is None:
            element = self.page.query_selector('iframe#mainFrame')
            frame = element.content_frame() if element else None
        if frame is None:
            raise ValueError('未找到 mainFrame，请先登录并打开晚归管理页面')
        if urlparse(self.page.url).netloc != urlparse(self.origin).netloc:
            raise ValueError('当前主页面不在学校系统中，请完成登录')
        return frame

    def goto(self, link, selector):
        f = self.frame()
        response = f.goto(self.safe_url(link), wait_until='domcontentloaded')
        if response and response.status >= 400:
            raise ValueError(f'页面访问失败 HTTP {response.status}')
        f.wait_for_selector(selector, state='attached')
        if not f.url.startswith(self.origin+BASE):
            raise ValueError('晚归页面跳转到登录或其他模块，请重新登录')
        return f

    def submit_query(self, f):
        self.check()
        with f.expect_navigation(wait_until='domcontentloaded'):
            f.locator('#searchForm input[type="submit"]').first.click()
        f.wait_for_selector('#contentTable', state='attached')

    def date_input(self, f, selector, value):
        element = f.locator(selector)
        element.evaluate('(e) => e.removeAttribute("readonly")')
        element.fill(value)
        element.dispatch_event('change')

    def query(self, f, values):
        form = f.locator('#searchForm')
        form.evaluate('''form => {
          for (const e of form.querySelectorAll('input[type=text], input:not([type]), select')) {
            if (e.matches('.pagination *')) continue;
            if (e.tagName==='SELECT') {
              if (Array.from(e.options).some(o=>o.value==='')) e.value='';
            } else e.value='';
          }
          if(form.querySelector('#pageNo')) form.querySelector('#pageNo').value='1';
        }''')
        for key, value in values.items():
            loc = f.locator('#searchForm #'+key)
            if loc.count() != 1:
                raise ValueError('查询字段缺失或重复：'+key)
            if loc.evaluate('(e)=>e.tagName') == 'SELECT':
                loc.select_option(value=value)
            else:
                self.date_input(f, '#searchForm #'+key, value)
        self.submit_query(f)

    def all_pages(self, parser):
        signatures, result = set(), []
        for _ in range(1000):
            self.check()
            f = self.frame()
            items = parser(f.content())
            # Include displayed rows even if filtering removes all candidates.
            signature = tuple(f.locator('#contentTable tbody input[name="ids"]').evaluate_all('(es)=>es.map(e=>e.value)'))
            if signature in signatures:
                raise ValueError('分页结果重复，停止以避免漏页或死循环')
            signatures.add(signature)
            result.extend(items)
            yield result, items
            next_link = f.locator('.pagination li:not(.disabled) > a').filter(has_text=re.compile('下一页'))
            if next_link.count() == 0:
                return
            if next_link.count() != 1:
                raise ValueError('下一页入口不唯一')
            with f.expect_navigation(wait_until='domcontentloaded'):
                next_link.click()
            f.wait_for_selector('#contentTable', state='attached')
        raise ValueError('分页超过限制，停止扫描')

    @read_step
    def form_page(self):
        f = self.goto(BASE+'nightLateApply/form', '#inputForm #studentId')
        links = f.locator('a').filter(has_text=re.compile('^晚归申请列表$'))
        if links.count() != 1:
            raise ValueError('无法唯一确定晚归申请列表地址')
        self.application_url = self.safe_url(links.get_attribute('href'))
        minimum = f.locator('#minDate').input_value() if f.locator('#minDate').count() else None
        maximum = f.locator('#maxDate').input_value() if f.locator('#maxDate').count() else None
        return f, minimum, maximum

    @read_step
    def applications(self, student_id):
        if self.application_url is None:
            self.form_page()
        f = self.goto(self.application_url, '#searchForm')
        self.query(f, {'studentId':student_id})
        result = []
        for accumulated, _ in self.all_pages(parse_applications):
            result = accumulated
        # Validate, do not trust a silently ignored query.
        if any(a['studentId'] != student_id for a in result):
            raise ValueError('申请查询学号未生效，停止处理')
        return result

    @read_step
    def detail(self, record):
        f = self.goto(record['detail_url'], '#inputForm')
        d = parse_detail(f.content(), record['studentId'])
        for k in ('username','departName','naturalclassName','grade','lateDate'):
            if d[k] != record[k]:
                raise ValueError('详情与列表不一致：'+k)
        return d

    @read_step
    def record_snapshot(self, start, end):
        candidates=[]
        for status in ('0','3'):
            f=self.goto(BASE+'nightLateRecord/excepList', '#searchForm')
            self.query(f,dict(beginDate=start,endDate=end,applyStatus=status,ioFlag='1'))
            group=[]
            for accumulated,_ in self.all_pages(lambda html:parse_records(html,start,end)):
                group=accumulated
                self.log(f'状态{status}已读取 {len(group)} 条候选，继续分页快照。')
            candidates.extend(group)
        return candidates

    def scan(self, start, end, limit):
        self.check()
        self.last_scan_plans=[]
        lo, hi = date.fromisoformat(start), date.fromisoformat(end)
        if lo > hi or (hi-lo).days > 366:
            raise ValueError('日期范围须前后有序且不超过367天')
        if not 1 <= limit <= 500:
            raise ValueError('每批数量须在1至500之间')
        self.log(f'扫描 {start} 至 {end}，本批最多 {limit} 条；此步骤不会保存或审核。')
        _, minimum, maximum = self.form_page()
        candidates=self.record_snapshot(start,end)
        plans, seen = self.last_scan_plans, set()
        rejected_ids=set()
        for record in candidates:
            self.check()
            rid = record['record_id']
            if rid in seen:
                continue
            seen.add(rid)
            prior = self.journal.get(rid)
            if prior:
                self.log(f'{record["studentId"]} {record["lateDate"]} 已有流水 {prior["state"]}，本次跳过；详见日志。')
                continue
            d = self.detail(record)
            if d['applyStatus'] != record.get('source_status','无申请') or d['ioFlag'] != '进':
                self.log('记录状态已变化，跳过：'+record['studentId'])
                continue
            begin, finish = interval(record['lateDate'], minimum, maximum)
            plan = dict(record, specialtyName=d['specialtyName'], beginDate=begin, endDate=finish,
                        lateType='1', markup='2', reason='1', phone=os.environ.get('LATE_RETURN_PHONE','').strip())
            apps = self.applications(plan['studentId'])
            if covering(apps, plan):
                self.log(f'{plan["studentId"]} 已有待审核或已通过申请覆盖该晚归时间，跳过。')
                continue
            if d['applyStatus']=='审核不通过':
                target=rejected_application(apps,plan)
                owner=self.journal.rejected_owner(target['id'])
                if owner:
                    self.log('该被拒申请已有删除流水，禁止借其他晚归记录再次删除：'+target['id']+'，原记录 '+owner)
                    continue
                if target['id'] in rejected_ids:
                    self.log('同一被拒申请已列入本批，其他关联晚归请执行后重新扫描：'+target['id'])
                    continue
                plan['rejected_application']=dict(target)
                plan['delete_target']=target['id']+' | '+target['beginDate']+' 至 '+target['endDate']
                rejected_ids.add(target['id'])
                self.log('预览待删除被拒申请：'+plan['delete_target'])
            adjusted=conflict_free_interval(plan['lateDate'],apps,plan['studentId'],minimum,maximum)
            if adjusted!=(begin,finish):
                plan.update(original_beginDate=begin,original_endDate=finish)
                begin,finish=adjusted
                plan.update(beginDate=begin,endDate=finish)
                self.log('避开已有申请的重叠区间，调整为：'+begin+' 至 '+finish)
            plans.append(plan)
            self.log(f'预览 {plan["studentId"]} {plan["username"]}：{begin} 至 {finish}')
            if len(plans)%10==0:
                self.report(plans,'扫描进度')
                self.maintenance.run()
                self.log('已核对10条，短暂间隔后继续扫描。')
                self.pause(2)
            if len(plans) >= limit:
                break
        self.report(plans, '预览')
        self.log(f'扫描完成，可执行 {len(plans)} 条。')
        return plans

    def select(self, f, key, value):
        self.check()
        dependent = {'departName':'specialtyName','specialtyName':'naturalclassName'}.get(key)
        if dependent:
            # Real supplied forms initially contain all options. Presence of an old
            # option does not prove that loadSpecialty/loadNaturalClass finished.
            f.evaluate('''dep => {
                if(window.__lateCascade?.observer) window.__lateCascade.observer.disconnect();
                const e=document.getElementById(dep);
                if(!e) throw Error('Missing dependent select: '+dep);
                const state={changed:false};
                state.observer=new MutationObserver(()=>{state.changed=true;});
                state.observer.observe(e.parentNode,{childList:true,subtree:true});
                window.__lateCascade=state;
            }''', dependent)
        label={'departName':'学院','specialtyName':'专业','naturalclassName':'班级','grade':'年级','lateType':'晚归类型','markup':'申请类型'}.get(key,key)
        self.log('正在选择'+label+'：'+value)
        try:
            f.wait_for_function('''([key,value]) => {
               const e=document.getElementById(key);
               return e && Array.from(e.options).some(o=>o.value===value);
            }''', arg=[key,value])
        except PlaywrightTimeout as exc:
            options=f.locator('#'+key).evaluate('(e)=>Array.from(e.options).map(o=>o.value)')
            sample='、'.join(options[:30])
            raise ValueError(label+'找不到目标选项：'+value+'；当前前30个选项：'+sample) from exc
        options=f.locator('#'+key).evaluate('(e)=>Array.from(e.options).map(o=>({value:o.value,text:o.textContent}))')
        selected_index=option_index(options,value)
        duplicates=sum(o['value']==value for o in options)
        if duplicates>1:
            self.log(label+'存在'+str(duplicates)+'个同值同名选项，按相同提交值选择。')
        f.locator('#'+key).select_option(index=selected_index)
        if dependent:
            try:
                f.wait_for_function('window.__lateCascade && window.__lateCascade.changed')
            finally:
                f.evaluate('window.__lateCascade?.observer?.disconnect()')
        f.wait_for_function('!window.jQuery || !window.jQuery.active')

    def fallback_form(self, plan):
        # A fresh real form contains the original full option sets supplied by
        # the site. Set only existing exact options together, so intermediate
        # cascade handlers cannot erase the user-authorized fallback class.
        f, minimum, maximum = self.form_page()
        validate_interval(plan['lateDate'],plan['beginDate'],plan['endDate'],minimum,maximum)
        f.locator('#studentId').fill(plan['studentId'])
        f.locator('#studentId').press('Tab')
        f.wait_for_function('!window.jQuery || !window.jQuery.active')
        indices={}
        for key in ('departName','specialtyName','naturalclassName','grade'):
            options=f.locator('#'+key).evaluate('(e)=>Array.from(e.options).map(o=>({value:o.value,text:o.textContent}))')
            try:
                indices[key]=option_index(options,plan[key])
            except ValueError as exc:
                raise ValueError('兜底表单缺少有效选项 '+key+'='+plan[key]+'；不会自行添加选项') from exc
        self.check()
        f.evaluate('''indices=>{
            for(const [key,index] of Object.entries(indices)){
                document.getElementById(key).selectedIndex=index;
            }
        }''',indices)
        self.log('已选用兜底：机器人工程 / 2025机器人1班；原年级保留为 '+plan['grade'])
        return f

    def fill_form(self, plan):
        f, minimum, maximum = self.form_page()
        validate_interval(plan['lateDate'],plan['beginDate'],plan['endDate'],minimum,maximum)
        f.locator('#studentId').fill(plan['studentId'])
        f.locator('#studentId').press('Tab')
        self.select(f, 'departName', plan['departName'])
        try:
            for key in ('specialtyName','naturalclassName'):
                self.select(f, key, plan[key])
        except (ValueError, PlaywrightTimeout) as exc:
            self.check()
            reason=str(exc).splitlines()[0]
            self.log('原专业或班级不可选，使用用户指定兜底：'+reason)
            effective=fallback_plan(plan,reason)
            plan.update(effective)
            f=self.fallback_form(plan)
        else:
            self.select(f, 'grade', plan['grade'])
        f.locator('#username').fill(plan['username'])
        for key in ('beginDate','endDate'):
            self.date_input(f, '#'+key, plan[key])
        for key in ('lateType','markup'):
            self.select(f, key, plan[key])
        for key in ('reason','phone'):
            f.locator('#'+key).fill(plan[key])
        # Wait for existing jQuery handlers/autocomplete rather than a fixed sleep.
        f.wait_for_function('!window.jQuery || !window.jQuery.active')
        self.check()
        for key in ('studentId','username','departName','specialtyName','naturalclassName','grade','beginDate','endDate','lateType','markup','reason','phone'):
            if f.locator('#'+key).input_value().strip() != plan[key]:
                raise ValueError('表单回读与预览不符：'+key)
        return f

    @read_step
    def validate_application(self, plan, application_id, audit=False):
        path = 'auditForm' if audit else 'detail'
        f = self.goto(BASE+'nightLateApply/'+path+'?'+urlencode({'id':application_id}), '#inputForm')
        values = fields(f.content())
        for key in ('studentId','username','departName','specialtyName','naturalclassName','grade','phone','reason'):
            if values.get(key) != plan[key]:
                raise ValueError('新增申请详情不匹配：'+key+'；停止审核，请人工核查')
        for key in ('beginDate','endDate'):
            # Audit page observed only displays minutes. The list was already verified to seconds.
            actual, expected = stamp(values.get(key,'')), stamp(plan[key])
            if actual != expected and not (len(values[key])==16 and actual==expected.replace(second=0)):
                raise ValueError('新增申请时间不匹配：'+key)
        if values.get('lateType') != '学习原因' or values.get('markup') != '补录':
            raise ValueError('新增申请的晚归或申请类型不匹配')
        if audit:
            if f.locator('#ids').input_value() != application_id:
                raise ValueError('审核表单 ID 不匹配')
            status = f.locator('.control-group').filter(has=f.locator('label',has_text='申请状态')).locator('.controls').inner_text().strip()
            if status != '待审核':
                raise ValueError('该申请不再处于待审核状态')
        return f

    def approve(self, plan, app_id):
        f = self.validate_application(plan, app_id, audit=True)
        self.check()
        self.journal.update(plan['record_id'], 'approving', app_id)
        confirmations = []

        def handle(dialog):
            if self.stop.is_set():
                dialog.dismiss()
            elif dialog.type == 'confirm' and dialog.message.strip() == '请确认是否审核通过？':
                confirmations.append(True)
                dialog.accept()
            elif dialog.type == 'alert':
                self.log('页面提示：'+dialog.message)
                dialog.accept()
            else:
                dialog.dismiss()

        self.page.on('dialog', handle)
        try:
            f.locator('input[type="submit"][value="通过"]').click()
            # Some deployments use jBox HTML confirmation instead of window.confirm.
            until = time.monotonic()+6
            while not confirmations and time.monotonic()<until:
                self.check()
                for scope in (self.page.main_frame, self.frame()):
                    boxes = scope.locator('.jbox:visible, .jbox-wrapper:visible')
                    for index in range(boxes.count()):
                        box = boxes.nth(index)
                        if '请确认是否审核通过' not in box.inner_text():
                            continue
                        buttons = box.locator('button, input[type="button"], a').filter(has_text=re.compile(r'^\s*(确定|确认|是)\s*$'))
                        if buttons.count()==1:
                            self.check()
                            buttons.click()
                            confirmations.append(True)
                            break
                    if confirmations:
                        break
                if not confirmations:
                    self.page.wait_for_timeout(150)
            if not confirmations:
                raise ValueError('未识别到预期审核确认框，停止并保留流水，请人工核查')
            # Wait for the submitted audit page to leave before querying another page.
            f.wait_for_function('!document.querySelector("#inputForm #ids")', timeout=20000)
        finally:
            self.page.remove_listener('dialog', handle)
        apps = self.applications(plan['studentId'])
        exact = [a for a in matching(apps,plan) if a['id']==app_id]
        if len(exact)!=1 or exact[0]['status']!='审核通过':
            raise ValueError('审核结果未能核实；禁止自动再次提交审核，请人工核查')
        self.journal.update(plan['record_id'],'done',app_id)

    def save_once(self, f, plan, old_ids):
        returned_html=''
        try:
            with f.expect_navigation(wait_until='domcontentloaded'):
                f.locator('#inputForm input[type="submit"]').click()
            returned_html=f.content()
            soup=BeautifulSoup(returned_html,'html.parser')
            notices={' '.join(e.get_text(' ',strip=True).split()) for e in soup.select('.alert, #messageBox, label.error')}
            for notice in sorted(notices):
                if notice: self.log('学校保存返回提示：'+notice[:1500])
            f.wait_for_selector('#contentTable',state='attached')
            return poll_added(lambda:self.applications(plan['studentId']),old_ids,plan,self.log,self.check,self.pause)
        except Exception:
            if not returned_html:
                try:
                    if self.page and not self.page.is_closed():
                        returned_html=f.content()
                        soup=BeautifulSoup(returned_html,'html.parser')
                        notices={' '.join(e.get_text(' ',strip=True).split()) for e in soup.select('.alert, #messageBox, label.error')}
                        for notice in sorted(notices):
                            if notice: self.log('保存未完成时的网页提示（不代表已保存）：'+notice[:1500])
                except Exception:
                    pass
            if returned_html:
                rid=re.sub(r'[^A-Za-z0-9_-]','_',plan['record_id'])[:80]
                path=self.data/('保存返回-'+self.session_id+'-'+rid+'.html')
                try:
                    path.write_text(returned_html,encoding='utf-8')
                    self.log('已保留学校保存返回页面（含学生资料，仅供本地核查）：'+str(path))
                except OSError:
                    pass
            raise


    @read_step
    def rejected_page(self, plan):
        f=self.goto(self.application_url, '#searchForm')
        self.query(f,{'studentId':plan['studentId']})
        target_id=plan['rejected_application']['id']
        for _,items in self.all_pages(parse_applications):
            if any(a['id']==target_id for a in items):
                rejected_application(items,plan)
                return self.frame()
        raise ValueError('被拒申请已不在列表，请重新扫描；不会删除')

    def confirm_delete(self, f, plan, target):
        # Only the observed visible bulk-delete control is used; no inferred endpoint.
        target_id=target['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',target_id):
            raise ValueError('申请ID格式异常，停止删除')
        row=f.locator('#contentTable tbody tr').filter(has=f.locator('input[name="ids"][value="'+target_id+'"]'))
        if row.count()!=1:
            raise ValueError('删除对象所在行不唯一')
        # Re-read the displayed row immediately before selecting it.
        rejected_application(parse_applications('<table id="contentTable"><tbody>'+row.evaluate('(e)=>e.outerHTML')+'</tbody></table>'),plan)
        boxes=f.locator('#searchForm input[type="checkbox"]')
        for i in range(boxes.count()):
            boxes.nth(i).uncheck()
        row.locator('input[name="ids"]').check()
        selected=f.locator('#searchForm input[name="ids"]:checked').evaluate_all('(es)=>es.map(e=>e.value)')
        if selected!=[target_id]:
            raise ValueError('当前选中项不等于唯一待删除申请，停止删除')
        button=f.locator('button').filter(has_text=re.compile(r'^\s*删除选中项\s*$'))
        expected="return doAction('deleteSelectedAll','','请确认是否删除选中项？')"
        if button.count()!=1 or (button.get_attribute('onclick') or '').strip().rstrip(';')!=expected:
            raise ValueError('删除按钮与已核对页面不一致，停止删除')
        confirmed=[]
        def handle(dialog):
            if not self.stop.is_set() and dialog.type=='confirm' and dialog.message.strip()=='请确认是否删除选中项？':
                confirmed.append(True);dialog.accept()
            elif dialog.type=='alert':
                self.log('删除页面提示：'+dialog.message);dialog.accept()
            else:dialog.dismiss()
        self.page.on('dialog',handle)
        try:
            self.check()
            with f.expect_navigation(wait_until='domcontentloaded'):
                button.click()
                until=time.monotonic()+6
                while not confirmed and time.monotonic()<until:
                    self.check()
                    for scope in (self.page.main_frame,self.frame()):
                        containers=scope.locator('.jbox:visible, .jbox-wrapper:visible')
                        for i in range(containers.count()):
                            box=containers.nth(i)
                            if '请确认是否删除选中项？' not in box.inner_text():continue
                            controls=box.locator('button, a').filter(has_text=re.compile(r'^\s*(确定|确认|是)\s*$'))
                            if controls.count()==1:
                                # Recheck the complete selected ID set at the last confirmation boundary.
                                if f.locator('#searchForm input[name="ids"]:checked').evaluate_all('(es)=>es.map(e=>e.value)')!=[target_id]:
                                    raise ValueError('确认前选中项发生变化，停止删除')
                                self.check();controls.click();confirmed.append(True);break
                        if confirmed:break
                    if not confirmed:self.page.wait_for_timeout(150)
                if not confirmed:raise ValueError('未识别到预期删除确认框，停止并保留流水')
        finally:
            self.page.remove_listener('dialog',handle)

    def delete_rejected(self, plan):
        before=self.applications(plan['studentId'])
        target=rejected_application(before,plan)
        if covering(before,plan):
            raise ValueError('执行前出现有效覆盖申请，停止删除，请重新扫描')
        f=self.rejected_page(plan)
        self.check()
        self.journal.begin(plan['record_id'],plan,state='deleting')
        self.log('开始删除唯一被拒申请：'+target['id']+'；删除不自动重试。')
        self.confirm_delete(f,plan,target)
        # Navigation is not proof of deletion. Re-read only; never resend delete.
        for attempt in range(3):
            after=self.applications(plan['studentId'])
            ids={a['id'] for a in after}
            if target['id'] not in ids:
                other_ids={a['id'] for a in before if a['id']!=target['id']}
                if not other_ids.issubset(ids):
                    raise ValueError('删除后其他申请也发生变化，停止补录，请人工核查')
                self.journal.update(plan['record_id'],'deleted')
                self.log('已核实旧申请删除：'+target['id'])
                return
            if attempt<2:self.pause(2)
        raise ValueError('删除结果尚未确认，保留deleting流水；不会重复删除或新建，请人工核查')

    def execute(self, plans):
        self.check()
        results = []
        try:
            for plan in plans:
                self.check()
                rid = plan['record_id']
                prior = self.journal.get(rid)
                if prior:
                    if prior['state']=='done':
                        self.log('已完成，跳过：'+plan['studentId'])
                        results.append(dict(plan,result='已有完成流水，跳过'))
                        continue
                    raise ValueError('存在未完成操作流水；请先人工核查，不会自动重复提交：'+rid)
                d = self.detail(plan)
                if d['applyStatus']!=plan.get('source_status','无申请') or d['ioFlag']!='进' or d['specialtyName']!=plan['specialtyName']:
                    raise ValueError('执行前学生信息或记录状态已变化，请重新扫描')
                before = self.applications(plan['studentId'])
                if covering(before,plan):
                    self.log('执行前发现已有覆盖申请，跳过：'+plan['studentId'])
                    results.append(dict(plan,result='已有覆盖申请，跳过'))
                    continue
                if overlapping(before,plan):
                    raise ValueError('预览区间与已有申请有重叠，请重新扫描以调整时间')
                replacing=bool(plan.get('rejected_application'))
                if plan.get('source_status','无申请')=='审核不通过' and not replacing:
                    raise ValueError('被拒记录缺少已预览的删除对象，不会执行')
                if replacing:
                    rejected_application(before,plan)
                    self.delete_rejected(plan)
                    d=self.detail(plan)
                    if d['applyStatus']!='无申请' or d['ioFlag']!='进':
                        raise ValueError('删除后晚归状态尚未变为无申请，停止并保留流水，请人工核查')
                    before=self.applications(plan['studentId'])
                    if covering(before,plan) or overlapping(before,plan):
                        raise ValueError('删除后已有其他有效申请，停止新建，请重新核查')
                old_ids = {a['id'] for a in before}
                f = self.fill_form(plan)
                self.check()
                if replacing:
                    self.journal.update_plan(rid,plan)
                    self.journal.update(rid,'saving')
                else:
                    self.journal.begin(rid,plan)  # Commit BEFORE any save request can leave the browser.
                self.log('开始保存：'+plan['studentId']+'；若中断请核对流水，切勿盲目重试。')
                added=self.save_once(f,plan,old_ids)
                app_id=added['id']
                self.journal.update(rid,'saved',app_id)
                self.approve(plan,app_id)
                self.maintenance.run(force_export=True)
                results.append(dict(plan,result='审核通过',application_id=app_id))
                self.log('已核实审核通过：'+plan['studentId']+'，申请ID '+app_id)
                if len(results)%10==0:
                    self.report(results,'执行进度')
                    self.log('已完成10条，短暂间隔后继续下一组。')
                    self.pause(3)
        except Exception as exc:
            if 'plan' in locals():
                results.append(dict(plan,result='停止：'+str(exc)))
            self.log('任务停止：'+str(exc))
            try:
                if self.page and not self.page.is_closed():
                    self.page.screenshot(path=str(self.data/f'异常-{self.session_id}.png'),full_page=True,timeout=5000)
            except Exception:
                pass
            raise
        finally:
            self.report(results,'执行结果')
        return results

    def report(self, items, kind):
        path = self.data/f'{kind}-{datetime.now():%Y%m%d-%H%M%S-%f}.csv'
        columns = ['record_id','studentId','username','departName','specialtyName','naturalclassName','grade','lateDate','beginDate','endDate','phone','reason','result','application_id','original_specialtyName','original_naturalclassName','fallback_reason','original_beginDate','original_endDate','source_status','delete_target']
        with path.open('w',newline='',encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f,fieldnames=columns,extrasaction='ignore')
            writer.writeheader()
            for item in items:
                # Avoid spreadsheet formulas from untrusted school text.
                writer.writerow({k: ("'"+str(v) if str(v).startswith(('=','+','-','@')) else v) for k,v in item.items()})
        self.log('报告已保存：'+str(path))
        return path

    def close(self):
        # Disconnects are expected during recovery; cleanup must still complete.
        context,self.context=self.context,None
        driver,self.pw=self.pw,None
        journal,self.journal=self.journal,None
        self.page=None
        try:
            if context:
                try:
                    context.close()
                except PlaywrightError:
                    pass
        finally:
            try:
                if driver:
                    try:
                        driver.stop()
                    except PlaywrightError:
                        pass
            finally:
                if journal:
                    journal.close()
