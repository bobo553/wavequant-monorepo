from pathlib import Path
import tempfile
import unittest

from wavequant.tdx import RECORD
from wavequant.tdx_browser import TdxBrowser, read_names


class TdxBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        folder=self.root/'T0002/hq_cache';folder.mkdir(parents=True)
        data=bytearray(50)
        for code,name in [('600104','上 汽 集团'),('688001','华兴源创'),('510300','基金')]:
            r=bytearray(360);r[:6]=code.encode();n=name.encode('gbk');r[31:31+len(n)]=n;data.extend(r)
        (folder/'shs.tnf').write_bytes(data)
        self.day=self.root/'vipdoc/sh/lday/sh600104.day';self.day.parent.mkdir(parents=True)
        self.day.write_bytes(b''.join(RECORD.pack(d,1000,1200,900,1100,1000,100,0) for d in (20260102,20260105)))
        self.browser=TdxBrowser(self.root,self.root/'cache')

    def test_full_catalog_names_and_missing_daily(self):
        c=self.browser.catalog();self.assertEqual(c['with_daily'],1)
        self.assertEqual(len(c['stocks']),2)
        self.assertEqual(c['stocks'][0]['name'],'上汽集团')
        self.assertEqual(c['stocks'][0]['bar_count'],2)
        self.assertFalse(c['stocks'][1]['has_data'])

    def test_raw_prices_and_asof_do_not_claim_backtest(self):
        v=self.browser.view('sh.600104','2026-01-02')
        self.assertEqual(len(v['bars']),1);self.assertEqual(v['bars'][0]['close'],11)
        self.assertIsNone(v['metrics']);self.assertEqual(v['markers'],[])
        self.assertEqual(v['price_basis'],'raw_unadjusted')
        self.assertEqual(v['sessions'],['2026-01-02','2026-01-05'])
        t=self.browser.theory('sh.600104','2026-01-02')
        self.assertEqual(t['events'],[])

    def test_input_validation_and_corrupt_records(self):
        for s in ('../600104','sh.510300','hk.600104','sh.688001'):
            with self.assertRaises(ValueError): self.browser.view(s,'2026-01-05')
        with self.assertRaises(ValueError): self.browser.view('sh.600104','2020-01-01')
        self.day.write_bytes(b'bad');self.browser._loaded_at=0
        self.assertEqual(self.browser.catalog()['stocks'][0]['status'],'invalid_daily')
        with self.assertRaises(ValueError): self.browser.view('sh.600104','2026-01-05')

    def test_refresh_uses_changed_file_signature(self):
        self.assertEqual(len(self.browser.view('sh.600104','2026-01-06')['bars']),2)
        with self.day.open('ab') as f:f.write(RECORD.pack(20260106,1000,1200,900,1100,1000,100,0))
        self.assertEqual(len(self.browser.view('sh.600104','2026-01-06')['bars']),3)

    def test_legacy_name_layout(self):
        p=self.root/'legacy.tnf';r=bytearray(314);r[:6]=b'600104';r[23:31]='上汽集团'.encode('gbk');p.write_bytes(bytes(50)+r)
        self.assertEqual(read_names(p)['600104'],'上汽集团')

    def test_bad_single_name_does_not_hide_other_names(self):
        p=self.root/'T0002/hq_cache/shs.tnf';data=bytearray(p.read_bytes());data[50+360+31:50+360+33]=b'\xb4\x00';p.write_bytes(data)
        self.assertEqual(read_names(p)['600104'],'上汽集团')

    def test_raw_geometry_survives_restart_and_invalidates_on_bar_change(self):
        from unittest.mock import patch
        first=self.browser.theory('sh.600104','2026-01-05')
        self.browser=TdxBrowser(self.root,self.root/'cache')
        with patch('wavequant.tdx_browser.lecture_drawing',side_effect=AssertionError('disk must be reused')):
            self.assertEqual(self.browser.theory('sh.600104','2026-01-05'),first)
        self.day.write_bytes(RECORD.pack(20260102,1000,1200,900,1100,1000,100,0)+RECORD.pack(20260105,1100,1400,1000,1300,1000,100,0))
        second=self.browser.theory('sh.600104','2026-01-05')
        self.assertNotEqual(first['lecture_drawing'],second['lecture_drawing'])

    def test_raw_theory_engine_change_cannot_relabel_old_imported_code(self):
        from unittest.mock import patch
        with patch('wavequant.tdx_backtest.TdxBacktester._engine_hashes',return_value={'changed':'code'}):
            with self.assertRaisesRegex(ValueError,'重启'):self.browser.theory('sh.600104','2026-01-05')
