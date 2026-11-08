import sys
import unittest
from pathlib import Path
from playwright.sync_api import TimeoutError as PlaywrightTimeout, Error as PlaywrightError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from late_browser import navigate_login

class NavigationTests(unittest.TestCase):
    def test_timeout_and_aborted_navigation_leave_manual_login_available(self):
        for error in (PlaywrightTimeout('timeout'), PlaywrightError('net::ERR_ABORTED')):
            class Page:
                def goto(self,*args,**kwargs): raise error
                def is_closed(self): return False
            messages=[]
            self.assertFalse(navigate_login(Page(),'https://example.test',messages.append))
            self.assertTrue(messages)

    def test_closed_window_is_still_a_real_startup_failure(self):
        class Page:
            def goto(self,*args,**kwargs): raise PlaywrightError('closed')
            def is_closed(self): return True
        with self.assertRaises(PlaywrightError):
            navigate_login(Page(),'https://example.test',lambda _:None)

    def test_success_stops_waiting_after_navigation_commit(self):
        class Page:
            def goto(self,url,*,wait_until,timeout):
                assert wait_until=='commit'
                assert timeout<=15000
        self.assertTrue(navigate_login(Page(),'https://example.test',lambda _:None))

if __name__=='__main__': unittest.main()
