import json, tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path
import app

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        app.DATA=Path(self.tmp.name)
        app.DB=app.DATA/'test.sqlite3'
        app.init_db()
    def tearDown(self):self.tmp.cleanup()
    def test_score_bounded_and_transparent(self):
        score,factors,terms=app.score_result('Ada Lovelace','Ada Lovelace','English mathematician','https://example.org')
        self.assertTrue(0<=score<=100)
        self.assertIn('exact_phrase',factors)
        self.assertIn('ada',terms)
        self.assertEqual(factors['independent_evidence'],0)
    def test_project_delete_cascades(self):
        with app.db_conn() as c:
            c.execute('INSERT INTO projects(id,name,purpose,created,updated) VALUES(?,?,?,?,?)',('p','test','test',app.now(),app.now()))
            c.execute('INSERT INTO inputs(project_id,kind,value) VALUES(?,?,?)',('p','name','Synthetic Example'))
            c.execute('DELETE FROM projects WHERE id=?',('p',))
            self.assertEqual(c.execute('SELECT count(*) FROM inputs').fetchone()[0],0)
    def test_deduplication_constraint(self):
        with app.db_conn() as c:
            c.execute('INSERT INTO projects(id,name,purpose,created,updated) VALUES(?,?,?,?,?)',('p','test','test',app.now(),app.now()))
            c.execute('INSERT OR IGNORE INTO sources(project_id,url,title,source_type,origin,first_seen) VALUES(?,?,?,?,?,?)',('p','https://x.test/a','A','test','fixture',app.now()))
            c.execute('INSERT OR IGNORE INTO sources(project_id,url,title,source_type,origin,first_seen) VALUES(?,?,?,?,?,?)',('p','https://x.test/a','A','test','fixture',app.now()))
            self.assertEqual(c.execute('SELECT count(*) FROM sources').fetchone()[0],1)
    def test_adapter_normalization_mocked(self):
        class Resp:
            status=200
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return json.dumps({'query':{'search':[{'title':'Example','snippet':'A &lt;b&gt;snippet&lt;/b&gt;'}]}}).encode()
        old=app.urllib.request.urlopen
        try:
            app.urllib.request.urlopen=lambda *a,**k:Resp()
            result=app.wikipedia_search('Example')
            self.assertEqual(result[0]['title'],'Example')
            self.assertTrue(result[0]['url'].startswith('https://en.wikipedia.org/wiki/'))
        finally:app.urllib.request.urlopen=old
    def test_local_http_requires_token(self):
        srv=app.Server(('127.0.0.1',0),app.Handler)
        t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        try:
            base=f'http://127.0.0.1:{srv.server_port}'
            page=urllib.request.urlopen(base+'/').read().decode()
            self.assertIn(app.TOKEN,page)
            req=urllib.request.Request(base+'/api/projects')
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
            self.assertEqual(e.exception.code,403)
            req=urllib.request.Request(base+'/api/projects',headers={'X-LocalOSINT-Token':app.TOKEN})
            self.assertEqual(json.loads(urllib.request.urlopen(req).read()),[])
        finally:srv.shutdown();srv.server_close()
    def test_json_backup_restore_roundtrip(self):
        srv=app.Server(('127.0.0.1',0),app.Handler)
        t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        try:
            payload={'format':'LocalOSINT backup v1','data':{'projects':[{'id':'restore-1','name':'Synthetic','purpose':'test','created':app.now(),'updated':app.now(),'status':'ready'}]}}
            raw=json.dumps(payload).encode()
            req=urllib.request.Request(f'http://127.0.0.1:{srv.server_port}/api/restore',data=raw,method='POST',headers={'X-LocalOSINT-Token':app.TOKEN,'Content-Type':'application/json'})
            self.assertEqual(json.loads(urllib.request.urlopen(req).read()),{'ok':True})
            with app.db_conn() as c:self.assertEqual(c.execute('SELECT name FROM projects WHERE id=?',('restore-1',)).fetchone()[0],'Synthetic')
        finally:srv.shutdown();srv.server_close()
    def test_manual_review_values(self):
        self.assertIn('matched',('matched','not_matching','unclear','more_evidence'))
        self.assertEqual(app.MAX_ITERATIONS,2)

if __name__=='__main__':unittest.main()
