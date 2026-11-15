"""Local lifecycle/integrity regressions; actual MVC HTTP is tested separately."""
from pathlib import Path
from unittest import mock
import io,json,unittest,uuid
import mvc_runtime as rt
ROOT=rt.BASE/'runtime'/('runtime-tests-'+uuid.uuid4().hex);ROOT.mkdir(parents=True)

class RuntimeTests(unittest.TestCase):
    def test_data_directory_cannot_escape_workspace(self):
        with self.assertRaises(ValueError):rt.StudioProcess({'folder':str(ROOT)},rt.BASE.parent/'outside')
    def test_published_source_tamper_refused_before_tool_admission(self):
        p=ROOT/'published';p.mkdir();(p/'QcmDemo.dll').write_bytes(b'fixture only')
        b={'status':'COMPILED','published':str(p),'assembly':str(p/'QcmDemo.dll'),'published_files_sha256':rt.inventory(p),'toolchain':{}}
        (p/'QcmDemo.dll').write_bytes(b'altered')
        with mock.patch.object(rt,'dotnet_toolchain') as sdk:
            with self.assertRaisesRegex(ValueError,'Published build changed'):rt.check_build(b)
            sdk.assert_not_called()
    def test_changed_sdk_refused(self):
        p=ROOT/'published2';p.mkdir();(p/'QcmDemo.dll').write_bytes(b'fixture only')
        b={'status':'COMPILED','published':str(p),'assembly':str(p/'QcmDemo.dll'),'published_files_sha256':rt.inventory(p),'toolchain':{'identity':'old'}}
        with mock.patch.object(rt,'dotnet_toolchain',return_value={'identity':'new'}):
            with self.assertRaisesRegex(ValueError,'SDK changed'):rt.check_build(b)
    def test_complete_logs_survive_buffer_limit_and_pid_reuse(self):
        records=[]
        for index in range(2):
            p=mock.MagicMock();p.pid=123;p.returncode=0;p.poll.return_value=0
            lines=['READY 12345\n']+[f'line {i}\n' for i in range(2500)]
            p.stdout=io.StringIO(''.join(lines));response=mock.MagicMock();response.__enter__.return_value.status=200
            build={'folder':str(ROOT),'published':str(ROOT),'assembly':str(ROOT/'fixture.dll'),'toolchain':{'path':'fixture-only'}}
            with mock.patch.object(rt,'check_build'),mock.patch.object(rt.subprocess,'Popen',return_value=p),mock.patch.object(rt.urllib.request,'urlopen',return_value=response):
                s=rt.StudioProcess(build,ROOT/('data'+str(index))).start();s.reader.join(timeout=3);s.stop();before=(s.session_dir/'server.json').read_bytes();s.stop()
                self.assertEqual((s.session_dir/'server.log').read_text(encoding='utf-8'),''.join(lines))
                self.assertLessEqual(len(s.logs),2000);self.assertEqual((s.session_dir/'server.json').read_bytes(),before);records.append(s.session_dir)
        self.assertNotEqual(records[0],records[1]);self.assertTrue(all((p/'server.log').is_file() for p in records))
    def test_server_exit_before_ready_is_reported_and_stopped(self):
        p=mock.MagicMock();p.pid=123;p.returncode=1;p.poll.return_value=1;p.stdout=io.StringIO('Storage locked by another server\n')
        build={'folder':str(ROOT),'published':str(ROOT),'assembly':str(ROOT/'fixture.dll'),'toolchain':{'path':'fixture-only'}}
        with mock.patch.object(rt,'check_build'),mock.patch.object(rt.subprocess,'Popen',return_value=p):
            s=rt.StudioProcess(build,ROOT/'failed')
            with self.assertRaisesRegex(RuntimeError,'Storage locked'):s.start()
            self.assertTrue(s.stopped);self.assertTrue((s.session_dir/'server.json').exists())
    def test_foreign_assembly_path_refused(self):
        with self.assertRaisesRegex(ValueError,'Unexpected assembly'):
            rt.check_build({'status':'COMPILED','published':str(ROOT),'assembly':str(ROOT/'other.dll')})
if __name__=='__main__':
    r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeTests))
    rt.save(ROOT/'results.json',{'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'scope':'Fixture lifecycle/integrity only, no actual MVC claim'})
    print(ROOT);raise SystemExit(not r.wasSuccessful())
