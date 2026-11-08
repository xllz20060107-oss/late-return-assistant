"""Chinese desktop UI. Playwright is created and used only by one worker."""
import json
import os
import queue
import sys
import threading
import traceback
from datetime import date, timedelta, datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from brand_assets import PHOTO_PNG

# Some Windows Python layouts need explicit Tcl/Tk paths. Frozen builds use
# PyInstaller's runtime hook and must keep its bundled paths.
if not getattr(sys,'frozen',False):
    for variable,folder in (('TCL_LIBRARY','tcl8.6'),('TK_LIBRARY','tk8.6')):
        candidate=Path(sys.base_prefix)/'tcl'/folder
        if candidate.is_dir():
            os.environ.setdefault(variable,str(candidate))


def home_dir():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent


class InstanceLock:
    def __init__(self, path):
        import msvcrt
        self.file = open(path,'a+b')
        self.file.seek(0)
        if not self.file.read(1):
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            self.file.close()
            raise RuntimeError('程序已在运行。请使用已打开的窗口，避免同时处理同一记录。')

    def close(self):
        import msvcrt
        self.file.seek(0)
        msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
        self.file.close()


class App:
    def __init__(self, root, data):
        self.root, self.data = root, data
        self.commands, self.events = queue.Queue(), queue.Queue()
        self.stop = threading.Event()
        self.plans, self.snapshot = [], None
        self.opened, self.busy, self.closing = False, False, False
        self.restart_pending=False
        self._maintenance_enabled=True
        self.root.title('晚归补录助手')
        self.root.geometry('1120x760')
        self.root.minsize(920,650)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10))
        style.configure('Title.TLabel',font=('Microsoft YaHei UI',19,'bold'),foreground='#133d65')
        style.configure('Accent.TButton',font=('Microsoft YaHei UI',10,'bold'),padding=(14,8))
        style.configure('Treeview',rowheight=29)
        style.configure('Treeview.Heading',font=('Microsoft YaHei UI',10,'bold'))
        outer = ttk.Frame(root,padding=22); outer.pack(fill='both',expand=True)
        header=ttk.Frame(outer)
        header.pack(fill='x',pady=(0,12))
        heading=ttk.Frame(header)
        heading.pack(side='left',fill='x',expand=True,anchor='n')
        self.heading_label=ttk.Label(heading,text='晚归补录助手',style='Title.TLabel')
        self.heading_label.pack(anchor='w')
        ttk.Label(heading,text='打开 Edge 并登录  →  选择日期、扫描预览  →  核对后执行补录与审核',foreground='#526577',wraplength=720).pack(anchor='w',pady=(6,0))
        self.brand_photo=tk.PhotoImage(data=PHOTO_PNG).subsample(14,14)
        self.portrait_label=ttk.Label(header,image=self.brand_photo)
        self.portrait_label.pack(side='right',anchor='ne',padx=(16,8))
        controls = ttk.LabelFrame(outer,text='处理范围',padding=12); controls.pack(fill='x')
        yesterday = (date.today()-timedelta(days=1)).isoformat()
        self.start = tk.StringVar(value=yesterday)
        self.end = tk.StringVar(value=yesterday)
        self.limit = tk.StringVar(value='1')
        for col,(label,var,width) in enumerate((('开始日期',self.start,13),('结束日期',self.end,13),('本批最多条数',self.limit,6))):
            ttk.Label(controls,text=label).grid(row=0,column=col*2,padx=(0,8))
            widget = ttk.Entry(controls,textvariable=var,width=width)
            widget.grid(row=0,column=col*2+1,padx=(0,18))
        ttk.Label(controls,text='日期格式：YYYY-MM-DD；首次建议1条',foreground='#6a7581').grid(row=1,column=0,columnspan=6,sticky='w',pady=(10,0))
        ttk.Label(outer,text='学习原因 / 补录 / 原因 1 / 电话由 LATE_RETURN_PHONE 配置；专业班级不可选时兜底：机器人工程 / 2025机器人1班',foreground='#274e6d').pack(anchor='w',pady=(12,8))
        toolbar = ttk.Frame(outer); toolbar.pack(fill='x',pady=(0,12))
        self.buttons = {}
        for key,label,callback in (
            ('open','① 打开 Edge 登录',lambda:self.command('open')),
            ('scan','② 扫描预览',self.scan),
            ('execute','③ 执行补录并审核',self.execute),
            ('restart','重启浏览器',self.restart_browser),
            ('stop','停止',self.request_stop),
            ('folder','打开日志与报告',lambda:os.startfile(str(self.data))),
            ('ranking','学生排行榜',lambda:self.command('ranking'))):
            b = ttk.Button(toolbar,text=label,command=callback,style='Accent.TButton' if key in ('open','scan','execute') else 'TButton')
            b.pack(side='left',padx=(0,8)); self.buttons[key]=b
        ttk.Label(outer,text='预览记录（扫描不删除、不保存、不审核；包含无申请和审核不通过记录）').pack(anchor='w',pady=(0,5))
        table_frame = ttk.Frame(outer); table_frame.pack(fill='both',expand=True)
        columns = ('studentId','username','lateDate','beginDate','endDate','naturalclassName','source_status','delete_target')
        self.tree = ttk.Treeview(table_frame,columns=columns,show='headings',height=7)
        for col,label,width in zip(columns,('学号','姓名','晚归时间','申请开始','申请结束','班级','原状态','待删除被拒申请ID及区间'),(140,80,164,164,164,170,100,480)):
            self.tree.heading(col,text=label); self.tree.column(col,width=width,minwidth=70)
        scroll = ttk.Scrollbar(table_frame,orient='vertical',command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set,xscrollcommand=xscroll.set)
        self.tree.grid(row=0,column=0,sticky='nsew'); scroll.grid(row=0,column=1,sticky='ns'); xscroll.grid(row=1,column=0,sticky='ew')
        table_frame.rowconfigure(0,weight=1); table_frame.columnconfigure(0,weight=1)
        self.status = tk.StringVar(value='尚未连接。请先打开 Edge，在新窗口中手动登录学校系统。')
        ttk.Label(outer,textvariable=self.status,foreground='#133d65',wraplength=1030).pack(anchor='w',pady=(10,5))
        self.logbox = tk.Text(outer,height=8,wrap='word',font=('Microsoft YaHei UI',9),state='disabled',bg='#f4f6f8',relief='flat')
        self.logbox.pack(fill='both',expand=False)
        ttk.Label(outer,text='诊断文件保留7天；学生排行榜和永久流水长期保留。保存或审核结果不明确时请先核查，不要删除流水。',foreground='#6a7581',wraplength=1030).pack(anchor='w',pady=(8,0))
        for var in (self.start,self.end,self.limit):
            var.trace_add('write',self.invalidate)
        self.worker = threading.Thread(target=self.work,daemon=False)
        self.worker.start()
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.refresh()
        self.root.after(100,self.poll)

    def values(self):
        return (self.start.get().strip(),self.end.get().strip(),int(self.limit.get()))

    def invalidate(self,*_):
        self.plans=[]; self.snapshot=None
        self.tree.delete(*self.tree.get_children())
        if self.opened and not self.busy:
            self.status.set('范围已更改，请重新扫描预览。')
        self.refresh()

    def refresh(self):
        states = dict(open=not self.opened and not self.busy,
                      scan=self.opened and not self.busy,
                      execute=bool(self.plans) and not self.busy,
                      stop=self.busy and not self.restart_pending,folder=True,
                      restart=self.opened and not self.restart_pending,ranking=not self.busy)
        for key,button in self.buttons.items():
            button.configure(state='normal' if states[key] and not self.closing else 'disabled')

    def command(self, name, payload=None):
        if self.busy or self.closing:
            return
        self.stop.clear(); self.busy=True
        self.status.set({'open':'正在启动专用 Edge 窗口…','scan':'正在扫描，请勿操作程序打开的网页…','execute':'正在保存和审核，请勿操作程序打开的网页…','ranking':'正在刷新学生补录次数排行榜…'}[name])
        self.commands.put((name,payload)); self.refresh()

    def scan(self):
        try:
            start,end,limit=self.values()
            if date.fromisoformat(start)>date.fromisoformat(end) or not 1<=limit<=500:
                raise ValueError()
        except (ValueError,TypeError):
            messagebox.showerror('范围有误','请填写有效日期（YYYY-MM-DD），开始日期不晚于结束日期，条数为1至500。')
            return
        self.invalidate()
        self.scan_values=(start,end,limit)
        self.command('scan',self.scan_values)

    def execute(self):
        try:
            same = self.snapshot == self.values()
        except ValueError:
            same = False
        if not same or not self.plans:
            self.invalidate(); return
        targets=[p for p in self.plans if p.get('rejected_application')]
        deletion='\n\n本批须先删除 '+str(len(targets))+' 条被拒申请：\n'+'\n'.join(p['studentId']+' '+p['username']+' | '+p['lateDate']+' | '+p.get('delete_target',p['rejected_application']['id']) for p in targets[:10]) if targets else ''
        if len(targets)>10:
            deletion+='\n其余 '+str(len(targets)-10)+' 条请在预览表最右侧或预览CSV核对。'
        if messagebox.askyesno('执行本批补录与审核',f'即将为预览中的 {len(self.plans)} 条记录保存补录申请并自动审核通过。\n\n专业或班级不可选时将改用机器人工程／2025机器人1班，原年级保留。\n\n请确认学生、日期和时间区间正确。{deletion}\n\n是否开始？'):
            frozen=json.loads(json.dumps(self.plans,ensure_ascii=False))
            self.command('execute',frozen)

    def request_stop(self):
        self.stop.set()
        self.status.set('已请求停止，等待当前页面操作结束；已提交操作不会撤销。')

    def restart_browser(self):
        if self.closing or self.restart_pending:
            return
        self.restart_pending=True
        self.stop.set()
        self.invalidate()
        self.busy=True
        self.status.set('已请求重启浏览器，等待当前页面操作结束；保留登录资料和操作流水。')
        self.commands.put(('restart',None))
        self.refresh()

    def work(self):
        browser=None
        maintainer=None
        if getattr(self,'_maintenance_enabled',False):
            from late_maintenance import Maintenance
            maintainer=Maintenance(self.data,lambda line:self.events.put(('log',line)))
            try:maintainer.run(force=True)
            except Exception as exc:self.events.put(('log','自动维护暂未完成：'+str(exc)))
        while True:
            try:name,payload=self.commands.get(timeout=60)
            except queue.Empty:
                if maintainer:
                    try:maintainer.run()
                    except Exception as exc:self.events.put(('log','自动维护暂未完成：'+str(exc)))
                continue
            try:
                if name=='close':
                    try:
                        if browser:
                            browser.close()
                    finally:
                        self.events.put(('closed',None))
                    return
                if self.closing:
                    continue
                if name in ('open','restart'):
                    if name=='restart':
                        self.events.put(('restarting',None))
                        if browser:
                            try: browser.close()
                            except Exception as exc:
                                self.events.put(('log','旧浏览器断开，继续释放连接：'+str(exc)))
                        browser=None
                        self.stop.clear()
                    from late_browser import SchoolBrowser
                    browser=SchoolBrowser(self.data,log=lambda line:self.events.put(('log',line)),stop=self.stop,maintenance=maintainer)
                    browser.open()
                    self.events.put(('opened',None))
                elif name=='scan':
                    plans=browser.scan(*payload)
                    self.events.put(('scanned',(payload,plans)))
                elif name=='execute':
                    browser.execute(payload)
                    self.events.put(('executed',None))
                elif name=='ranking':
                    path=maintainer.run(force=True)
                    if maintainer.last_error or not path:
                        self.events.put(('rank_warning',maintainer.last_error or '排行榜尚未生成，请查看日志。'))
                    else:self.events.put(('ranking_ready',str(path)))
            except Exception as exc:
                if name=='close':
                    return
                if name in ('open','restart') and browser:
                    try: browser.close()
                    except Exception: pass
                    browser=None
                try:
                    trace=traceback.format_exc()
                    now=datetime.now()
                    with (self.data/f'错误详情-{now:%Y%m%d}.txt').open('a',encoding='utf-8') as f:
                        f.write(f'\n{now:%Y-%m-%d %H:%M:%S}\n'+trace)
                    (self.data/'错误详情.txt').write_text(f'最近错误（{now:%Y-%m-%d %H:%M:%S}）\n'+trace,encoding='utf-8')
                except Exception:
                    pass
                if name=='scan' and browser and browser.last_scan_plans and not self.stop.is_set():
                    browser.report(browser.last_scan_plans,'扫描中断已核对记录')
                    self.events.put(('scanned',(payload,list(browser.last_scan_plans))))
                    self.events.put(('warning','扫描中断，已保留前面核对完成的记录。可先处理预览，或重启浏览器后重新扫描。原因：'+str(exc)))
                else:
                    self.events.put(('error',(name,str(exc))))
            finally:
                self.events.put(('idle',None))

    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='log':
                    self.logbox.configure(state='normal'); self.logbox.insert('end',data+'\n'); self.logbox.see('end'); self.logbox.configure(state='disabled')
                elif kind=='restarting':
                    self.opened=False
                    self.status.set('正在关闭旧连接并重新打开 Edge；不会删除操作流水。')
                elif kind=='opened':
                    self.opened=True
                    self.restart_pending=False
                    self.status.set('请在新 Edge 窗口中登录学校系统，打开晚归管理，然后点击“扫描预览”。')
                elif kind=='scanned':
                    if self.restart_pending or self.closing:
                        continue
                    values,plans=data
                    try: unchanged=self.values()==values
                    except ValueError: unchanged=False
                    if unchanged:
                        self.snapshot=values; self.plans=plans
                        for plan in plans:
                            self.tree.insert('','end',values=[plan.get(c,'') for c in self.tree['columns']])
                        self.status.set(f'扫描完成：可执行 {len(plans)} 条。请核对预览；完整信息见预览 CSV 报告。')
                    else:
                        self.invalidate(); self.status.set('扫描期间范围发生变化，本次预览已作废，请重新扫描。')
                elif kind=='executed':
                    self.invalidate(); self.status.set('本批完成。结果已写入报告；继续处理请重新扫描。')
                elif kind=='ranking_ready':
                    self.status.set('学生补录次数排行榜已更新。')
                    try:os.startfile(data)
                    except OSError:messagebox.showinfo('排行榜已生成','文件位置：'+data)
                elif kind=='rank_warning':
                    self.status.set('排行榜未能刷新，累计成功记录仍在永久流水中。')
                    if not self.closing:messagebox.showwarning('排行榜未更新',data+'\n请关闭已打开的Excel文件后重试。')
                elif kind=='warning':
                    self.status.set(data)
                    if not self.closing and not self.restart_pending:
                        messagebox.showwarning('保留部分扫描结果',data)
                elif kind=='error':
                    name,message=data
                    if name in ('open','restart'):
                        self.opened=False; self.restart_pending=False
                    self.invalidate()
                    if self.restart_pending:
                        self.status.set('当前任务已停止，正在重启浏览器…')
                    else:
                        self.status.set('任务停止：'+message)
                        if not self.closing:
                            messagebox.showerror('任务停止',message+'\n\n可以点击“重启浏览器”重新连接。保存或审核结果不明确时，请先核对学校系统，不要删除操作流水。')
                elif kind=='idle':
                    self.busy=self.restart_pending; self.refresh()
                elif kind=='closed':
                    self.root.destroy(); return
        except queue.Empty:
            pass
        self.root.after(100,self.poll)

    def close(self):
        if self.closing:
            return
        if self.busy and not messagebox.askyesno('停止并退出','任务正在运行。退出会请求停止；已提交的保存或审核不会撤销。是否退出？'):
            return
        self.closing=True; self.stop.set(); self.refresh()
        self.status.set('正在停止并关闭专用浏览器，请稍候…')
        self.commands.put(('close',None))


def main():
    root=tk.Tk()
    data=home_dir()/'data'
    smoke='--smoke-test' in sys.argv
    if smoke:
        data=Path(sys.argv[sys.argv.index('--smoke-test')+1]).resolve()
    try:
        data.mkdir(parents=True,exist_ok=True)
        lock=InstanceLock(data/'instance.lock')
    except Exception as exc:
        messagebox.showerror('无法启动',str(exc)); root.destroy(); return
    app=App(root,data)
    if smoke:
        def finish():
            evidence={'title':root.title(),'buttons':list(app.buttons),'preview_rows':len(app.tree.get_children())}
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='msedge',headless=True)
                    page=browser.new_page()
                    page.set_content('<title>Local smoke test</title><input id="test">')
                    page.locator('#test').fill('OK')
                    evidence['edge_driver']=page.locator('#test').input_value()
                    browser.close()
            except Exception as exc:
                evidence['error']=str(exc)
            (data/'smoke.json').write_text(json.dumps(evidence,ensure_ascii=False),encoding='utf-8')
            app.close()
        root.after(700,finish)
    try:
        root.mainloop()
        app.worker.join(timeout=35)
    finally:
        lock.close()


if __name__=='__main__':
    main()
