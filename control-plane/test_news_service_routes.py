"""Exercise the real handler in a temporary HTTP server without importing Ariadne's live stores."""
import ast
import json
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, urlsplit, parse_qs

import news_service

class RouteTests(unittest.TestCase):
    def test_isolated_page_routes_and_source_security(self):
        project = Path(__file__).resolve().parent
        server_file = project / 'server.py'
        if not server_file.exists():
            server_file = Path(r'D:\Downloads\Ariadne\control-plane\server.py')
        source = server_file.read_text(encoding='utf-8')
        tree = ast.parse(source)
        handler = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'AriadneHandler')
        code = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),handler], type_ignores=[])
        ns = dict(BaseHTTPRequestHandler=BaseHTTPRequestHandler, Path=Path, json=json, sqlite3=sqlite3, re=re, datetime=datetime,
                  urlparse=urlparse, urlsplit=urlsplit, parse_qs=parse_qs, PUBLIC_ORIGIN='https://ariadne.dia.net.au',
                  VIDEO_RENDERER_URL='http://127.0.0.1:1', GODS_EYE_VIEW_URL='http://127.0.0.1:1',
                  SHUTDOWN_REQUESTED=False, _expire_sessions=lambda: None)
        exec(compile(ast.fix_missing_locations(code), '<isolated-handler>', 'exec'), ns)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for f in ('news-service.html','news-service.js','news-service.css'):
                (root/f).write_bytes((project/f).read_bytes())
            ns['ROOT']=root
            http=ThreadingHTTPServer(('127.0.0.1',0),ns['AriadneHandler'])
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{http.server_port}'
            def request(path, data=None, headers=None):
                h={'X-Forwarded-Proto':'https', **(headers or {})}
                if data is not None: h['Content-Type']='application/json'
                req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
                return urllib.request.urlopen(req,timeout=5)
            try:
                with request('/news-service') as r:self.assertIn(b'News Service',r.read())
                with request('/api/news-service/sources') as r:self.assertEqual(json.load(r)['sources'],[])
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/news-service/sources',{'action':'add','url':'@x','destination':'ai_noise'})
                self.assertEqual(error.exception.code,403)
                headers={'Origin':'https://ariadne.dia.net.au','Sec-Fetch-Site':'same-origin'}
                cid='UC'+'a'*22
                with patch.object(news_service,'resolve_channel',return_value=(cid,'Fixture Creator','@fixture','https://yt3.googleusercontent.com/avatar')):
                    with request('/api/news-service/sources',{'action':'add','url':'@fixture','destination':'ai_noise'},headers) as r:self.assertTrue(json.load(r)['ok'])
                with request('/api/news-service/sources') as r:self.assertEqual(json.load(r)['sources'][0]['id'],cid)
                with request('/api/news-service/sources',{'action':'friendly_name','id':cid,'friendly_name':'Old favourite'},headers) as r:self.assertTrue(json.load(r)['ok'])
                with patch.object(news_service,'resolve_channel',return_value=(cid,'Renamed Creator','@renamed','https://yt3.googleusercontent.com/new')):
                    with request('/api/news-service/sources',{'action':'refresh_identity','id':cid},headers) as r:self.assertTrue(json.load(r)['ok'])
                with request('/api/news-service/sources') as r:
                    row=json.load(r)['sources'][0]
                    self.assertEqual((row['name'],row['handle'],row['friendly_name']),('Renamed Creator','@renamed','Old favourite'))
                    self.assertEqual(row['thumbnail_url'],'https://yt3.googleusercontent.com/new')
                service=news_service.NewsService(root/'runtime'/'news-service.sqlite3')
                service.cache_publication(dict(id='x',source_id=cid,original_url='https://youtube.com/watch?v=x',creator='Fixture Creator',title='Fixture',published_at='2026-01-01T00:00:00Z'))
                with request('/api/news-service/publications?category=ai_noise') as r:self.assertEqual(json.load(r)['publications'][0]['title'],'Fixture')
                with request('/api/news-service/publications?category=thailand_news') as r:self.assertEqual(json.load(r)['publications'],[])
            finally:
                http.shutdown();http.server_close();thread.join()

if __name__=='__main__': unittest.main()
