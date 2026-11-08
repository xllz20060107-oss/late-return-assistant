"""Anonymized local school-like pages. Never accesses the university."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from html import escape
import threading
import re

BASE = '/sahManage/a/apply/'
RECORDS = [dict(record_id='r1', studentId='001', username='测试甲', departName='测试学院', specialtyName='测试专业', naturalclassName='测试班', grade='2025', lateDate='2026-10-24 23:45:12'), dict(record_id='r2', studentId='002', username='测试乙', departName='测试学院', specialtyName='测试专业', naturalclassName='测试班', grade='2025', lateDate='2026-10-24 00:07:39')]


def detail(data):
    return ''.join(f'<div class="control-group"><label for="{k if k != "status" else ""}">{k if k != "status" else "申请状态"}</label><div class="controls">{escape(str(v))}</div></div>' for k,v in data.items())


def query_form(action, body):
    return f'''<form id="searchForm" action="{action}" method="post"><input id="pageNo" name="pageNo" value="1" type="hidden"><input id="pageSize" name="pageSize" value="15" type="hidden"><input id="studentId" name="studentId"><input id="beginDate" name="beginDate" readonly><input id="endDate" name="endDate" readonly><select id="applyStatus" name="applyStatus"><option value=""></option><option value="0">无申请</option></select><select id="ioFlag" name="ioFlag"><option value=""></option><option value="1">进</option></select><input id="btnSubmit" type="submit" value="查询">{body}</form><script>function page(n,s){{document.querySelector('#pageNo').value=n;document.querySelector('#searchForm').submit();}}</script>'''


class Site:
    def __init__(self):
        self.apps = []
        self.writes = []
        self.bad_saved = False
        self.bad_detail = False
        self.fail_save_response = False
        self.jbox = False
        self.slow_prefilled = False
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.serve({})

            def do_POST(self):
                data = parse_qs(self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode())
                self.serve({k:v[0] for k,v in data.items()})

            def serve(self, data):
                url = urlparse(self.path)
                path = url.path
                args = {k:v[0] for k,v in parse_qs(url.query).items()}
                if path == '/sahManage/a':
                    body = f'<iframe id="mainFrame" name="mainFrame" src="{BASE}nightLateRecord/excepList"></iframe>'
                elif path.endswith('nightLateRecord/excepList'):
                    n = int(data.get('pageNo','1'))
                    r = RECORDS[min(n-1,len(RECORDS)-1)]
                    cells = ''.join(f'<td>{escape(str(r[k]))}</td>' for k in ('username','departName','naturalclassName','grade','lateDate'))
                    table = f'<table id="contentTable"><tbody><tr><td><input name="ids" value="{r["record_id"]}"></td><td><a href="{BASE}nightLateRecord/detail?id={r["record_id"]}">{r["studentId"]}</a></td>{cells}<td>进</td><td>无申请</td></tr></tbody></table>'
                    pager = f'<div class="pagination"><ul><li><a href="javascript:;" onclick="page({n+1},15)">下一页 »</a></li></ul></div>' if n < len(RECORDS) else '<div class="pagination"><ul><li class="disabled"><a>下一页 »</a></li></ul></div>'
                    body = query_form(path, table+pager)
                elif path.endswith('nightLateRecord/detail'):
                    r = dict(next(r for r in RECORDS if r['record_id']==args['id']), applyStatus='无申请', ioFlag='进')
                    if site.bad_detail:
                        r['studentId'] = 'OTHER'
                    body = '<form id="inputForm">'+ detail(r) + '</form>'
                elif path.endswith('nightLateApply/form'):
                    inputs = ''.join(f'<input id="{k}" name="{k}">' for k in ('studentId','username','beginDate','endDate','phone'))
                    body = f'''<ul class="nav-tabs"><li><a href="{BASE}nightLateApply/list">晚归申请列表</a></li></ul><form id="inputForm" action="{BASE}nightLateApply/save" method="post">{inputs}<input id="minDate" value="2026-08-30 00:00:00" type="hidden"><input id="maxDate" value="2027-01-15 23:59:59" type="hidden"><select id="departName" name="departName" onchange="setTimeout(()=>{{document.querySelector('#specialtyName').innerHTML='<option value=\"\"></option><option>测试专业</option>'; document.querySelector('#naturalclassName').innerHTML='<option value=\"\"></option>';}},100)"><option value=""></option><option>测试学院</option></select><select id="specialtyName" name="specialtyName" onchange="setTimeout(()=>document.querySelector('#naturalclassName').innerHTML='<option value=\"\"></option><option>测试班</option>',100)"><option value=""></option></select><select id="naturalclassName" name="naturalclassName"><option value=""></option></select><select id="grade" name="grade"><option>2025</option></select><select id="lateType" name="lateType"><option value="1">学习原因</option><option value="4">其他</option></select><select id="markup" name="markup"><option value="2">补录</option><option value="1">正常</option></select><textarea id="reason" name="reason"></textarea><input id="btnSubmit" type="submit" value="保 存"></form>'''
                    # Keep inline attribute quoting valid and simulate asynchronous cascading options.
                    body = re.sub(r'<select id="departName"[\s\S]*?</select>', '<select id="departName" name="departName"><option value=""></option><option>测试学院</option></select>', body)
                    body = re.sub(r'<select id="specialtyName"[\s\S]*?</select>', '<select id="specialtyName" name="specialtyName"><option value=""></option></select>', body)
                    body += """<input type="hidden" id="chainReady" name="chainReady" value="0" form="inputForm"><script>
                    document.querySelector('#departName').addEventListener('change',()=>setTimeout(()=>{
                      document.querySelector('#specialtyName').replaceChildren(new Option('',''),new Option('测试专业','测试专业'));
                      document.querySelector('#naturalclassName').replaceChildren(new Option('',''));
                      document.querySelector('#chainReady').value='1';
                    },100));
                    document.querySelector('#specialtyName').addEventListener('change',()=>setTimeout(()=>{
                      document.querySelector('#naturalclassName').replaceChildren(new Option('',''),new Option('测试班','测试班'));
                      document.querySelector('#chainReady').value='2';
                    },100));
                    </script>"""
                    if site.slow_prefilled:
                        body=body.replace('},100)', '},1500)')
                        body=body.replace('<select id="specialtyName" name="specialtyName"><option value=""></option></select>', '<select id="specialtyName" name="specialtyName"><option value=""></option><option>测试专业</option></select>')
                        body=body.replace('<select id="naturalclassName" name="naturalclassName"><option value=""></option></select>', '<select id="naturalclassName" name="naturalclassName"><option value=""></option><option>测试班</option></select>')
                elif path.endswith('nightLateApply/save'):
                    site.writes.append(('save',dict(data)))
                    app = dict(data, id='a'+str(len(site.apps)+1), status='待审核', lateType='学习原因', markup='补录', createDate='2026-10-28 00:00:00')
                    if site.bad_saved:
                        app['phone']='WRONG'
                    site.apps.append(app)
                    if site.fail_save_response:
                        body = '<p>保存后连接异常</p>'
                    else:
                        self.send_response(302); self.send_header('Location', BASE+'nightLateApply/list'); self.end_headers(); return
                elif path.endswith('nightLateApply/list'):
                    apps = [a for a in site.apps if not data.get('studentId') or a['studentId']==data['studentId']]
                    table = '<table id="contentTable"><tbody>'
                    for a in apps:
                        names = ('studentId','username','departName','naturalclassName','grade','beginDate','endDate','status','lateType','markup','createDate')
                        cells = ''.join(f'<td>{escape(a[k])}</td>' for k in names)
                        table += f'<tr><td><input name="ids" value="{a["id"]}"></td>{cells}<td><a href="{BASE}nightLateApply/auditForm?id={a["id"]}">审核</a></td></tr>'
                    table += '</tbody></table><div class="pagination"><ul><li class="disabled"><a>下一页 »</a></li></ul></div>'
                    body = query_form(path, table)
                elif path.endswith('nightLateApply/auditForm') or path.endswith('nightLateApply/detail'):
                    a = next(a for a in site.apps if a['id']==args['id'])
                    body = f'<form id="inputForm" action="{BASE}nightLateApply/audit" method="post"><input id="ids" name="ids" value="{a["id"]}" type="hidden"><input id="actType" name="actType" type="hidden">'+detail(a)+'<input id="auditReason" name="auditReason"><input type="submit" value="通过" onclick="document.querySelector(\'#actType\').value=\'passed\';return confirm(\'请确认是否审核通过？\')"></form>'
                    if site.jbox:
                        body = body.replace("document.querySelector('#actType').value='passed';return confirm('请确认是否审核通过？')", "document.querySelector('#actType').value='passed'; document.querySelector('#modal').style.display='block'; return false")
                        body += '<div id="modal" class="jbox" style="display:none"><div>请确认是否审核通过？</div><button onclick="document.querySelector(\'#inputForm\').submit()">确定</button></div>'
                elif path.endswith('nightLateApply/audit'):
                    site.writes.append(('audit', dict(data)))
                    a = next(a for a in site.apps if a['id']==data['ids'])
                    if data.get('actType') == 'passed':
                        a['status'] = '审核通过'
                    self.send_response(302); self.send_header('Location', BASE+'nightLateApply/list'); self.end_headers(); return
                else:
                    self.send_error(404); return
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(('<html><body>'+body+'</body></html>').encode())

        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.origin = 'http://127.0.0.1:'+str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
