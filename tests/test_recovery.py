import sys
import unittest
import threading
import queue
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from late_browser import retry_read, poll_added, SchoolBrowser, Stopped
from app import App

class RecoveryTests(unittest.TestCase):
    def test_read_timeout_retries_but_validation_error_does_not(self):
        calls=[]
        def read():
            calls.append(1)
            if len(calls)<3: raise PWTimeout('slow query')
            return 'rows'
        self.assertEqual(retry_read(read,lambda _:None,lambda:None,lambda _:None),'rows')
        self.assertEqual(len(calls),3)
        def invalid(): raise ValueError('identity mismatch')
        with self.assertRaises(ValueError): retry_read(invalid,lambda _:None,lambda:None,lambda _:None)

    def test_stop_and_closed_browser_do_not_loop(self):
        calls=[]
        def read(): calls.append(1);raise PWError('Target page, context or browser has been closed')
        with self.assertRaises(PWError): retry_read(read,lambda _:None,lambda:None,lambda _:None)
        self.assertEqual(len(calls),1)
        def stopped(): raise Stopped('stop')
        with self.assertRaises(Stopped): retry_read(read,lambda _:None,stopped,lambda _:None)
        self.assertEqual(len(calls),1)

    def test_poll_new_application_repeats_only_reads(self):
        plan=dict(studentId='001',username='测试',beginDate='2026-10-27 23:00:00',endDate='2026-10-28 00:00:00')
        app=dict(plan,id='new1',lateType='学习原因',markup='补录',status='待审核')
        calls=[]
        def read():
            calls.append(1)
            return [] if len(calls)<3 else [app]
        self.assertEqual(poll_added(read,set(),plan,lambda _:None,lambda:None,lambda _:None)['id'],'new1')
        self.assertEqual(len(calls),3)
        with self.assertRaises(ValueError): poll_added(lambda:[app,dict(app,id='new2')],set(),plan,lambda _:None,lambda:None,lambda _:None)

    def test_close_already_closed_context_is_idempotent(self):
        closed=[]
        class Context:
            def close(self): closed.append('context'); raise PWError('Target closed')
        class Driver:
            def stop(self): closed.append('driver')
        class Journal:
            def close(self): closed.append('journal')
        b=SchoolBrowser.__new__(SchoolBrowser)
        b.context=Context(); b.pw=Driver(); b.journal=Journal()
        b.close(); b.close()
        self.assertEqual(closed,['context','driver','journal'])

    def test_worker_restart_creates_new_browser_without_exiting_app(self):
        created=[]
        class Browser:
            def __init__(self,*args,**kwargs): created.append(self)
            def open(self): pass
            def close(self): pass
        a=App.__new__(App)
        a.data=Path('.');a.commands=queue.Queue();a.events=queue.Queue();a.stop=threading.Event();a.closing=False
        for cmd in ('open','restart','close'): a.commands.put((cmd,None))
        with patch('late_browser.SchoolBrowser',Browser): a.work()
        self.assertEqual(len(created),2)
        events=[]
        while not a.events.empty(): events.append(a.events.get()[0])
        self.assertEqual(events.count('opened'),2)
        self.assertIn('closed',events)

class GuiRecoveryTests(unittest.TestCase):
    def test_restart_during_scan_stops_worker_and_discards_stale_preview(self):
        import tempfile
        import tkinter as tk
        import time
        created=[];entered=threading.Event();gate=threading.Event()
        class Browser:
            def __init__(self,*args,**kwargs):
                self.stop=kwargs['stop'];self.last_scan_plans=[];self.closed=False;created.append(self)
            def open(self):
                if len(created)>1: gate.wait(3)
            def close(self): self.closed=True
            def scan(self,*args):
                entered.set();self.stop.wait(3)
                raise Stopped('scan stopped')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as td:
            root=tk.Tk()
            with patch('late_browser.SchoolBrowser',Browser):
                a=App(root,Path(td))
                try:
                    def pump(until):
                        deadline=time.monotonic()+3
                        while not until() and time.monotonic()<deadline:
                            root.update();time.sleep(.01)
                        self.assertTrue(until())
                    a.command('open');pump(lambda:a.opened and not a.busy)
                    a.command('scan',a.values());self.assertTrue(entered.wait(2))
                    a.restart_browser()
                    self.assertTrue(a.restart_pending)
                    a.events.put(('scanned',(a.values(),[{'studentId':'stale'}])))
                    gate.set();pump(lambda:a.opened and not a.restart_pending and not a.busy)
                    self.assertEqual(len(created),2)
                    self.assertTrue(created[0].closed)
                    self.assertEqual(a.plans,[])
                    self.assertEqual(a.tree.get_children(),())
                    self.assertTrue(root.winfo_exists())
                finally:
                    a.closing=True;a.stop.set();gate.set();a.commands.put(('close',None));a.worker.join(3);root.destroy()

if __name__=='__main__': unittest.main()
