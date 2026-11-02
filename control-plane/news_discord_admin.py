"""Authenticated News Service administration; no secrets leave the server."""
import hashlib
import hmac
import http.client
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie

import credential_store
from news_discord_local import REFERENCES, validate_webhook, format_publication, discord_transport
from news_discord_delivery import DeliveryStore, DeliveryError

ADMIN_REF = 'ARIADNE_NEWS_ADMIN'
COOKIE = '__Host-ariadne-news-admin'
ORIGIN = 'https://ariadne.dia.net.au'
sessions = {}
failures = []
lock = threading.RLock()

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 600000).hex()

def check_password(value):
    try:
        stored = credential_store.get_credential(ADMIN_REF, allow_environment=False)
        salt, digest = stored.split(':')
        return bool(value) and hmac.compare_digest(password_hash(value, salt), digest)
    except (ValueError, TypeError, UnicodeError):
        return False

def session(handler):
    try:
        cookie = SimpleCookie(); cookie.load(handler.headers.get('Cookie', ''))
        token = cookie[COOKIE].value
    except (KeyError, ValueError):
        return None
    with lock:
        record = sessions.get(token)
        if not record or record['expires'] < time.time():
            sessions.pop(token, None); return None
        return record

def respond(handler, status, value, cookie=None):
    data = json.dumps(value, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    if cookie: handler.send_header('Set-Cookie', cookie)
    handler.end_headers(); handler.wfile.write(data)

def connection_test(secret):
    path = validate_webhook(secret)
    conn = http.client.HTTPSConnection('discord.com', timeout=12)
    try:
        conn.request('GET', path, headers={'User-Agent':'Ariadne-NewsService/1.0'})
        response = conn.getresponse(); raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            return False, 'Discord rejected the connection (HTTP %s).' % response.status
        item = json.loads(raw)
        if not isinstance(item, dict) or not item.get('id'):
            return False, 'Discord returned an unexpected response.'
        return True, 'Connection successful.'
    except Exception:
        return False, 'Connection failed. Check the saved webhook and network.'
    finally: conn.close()

def handle(handler, path, database):
    """Return True if request was handled. Only this module's paths are accepted."""
    if not path.startswith('/api/news-admin/'): return False
    method = handler.command
    if handler.headers.get('X-Forwarded-Proto') != 'https':
        respond(handler, 403, {'ok':False,'message':'HTTPS required.'}); return True
    if method == 'GET' and path == '/api/news-admin/session':
        record = session(handler)
        respond(handler, 200, {'ok':True, 'authenticated':bool(record), **({'csrf':record['csrf']} if record else {})}); return True
    if method != 'POST':
        respond(handler, 404, {'ok':False,'message':'Not found.'}); return True
    if handler.headers.get('Origin') != ORIGIN or handler.headers.get('Sec-Fetch-Site') != 'same-origin' or handler.headers.get('Content-Type','').split(';')[0].strip() != 'application/json':
        respond(handler, 403, {'ok':False,'message':'Request origin rejected.'}); return True
    try:
        length = int(handler.headers.get('Content-Length','0'))
        if length < 1 or length > 16384: raise ValueError()
        body = json.loads(handler.rfile.read(length).decode('utf-8'))
        if not isinstance(body, dict): raise ValueError()
    except (ValueError, UnicodeError):
        respond(handler, 400, {'ok':False,'message':'Invalid request.'}); return True
    now = time.time()
    if path == '/api/news-admin/login':
        with lock:
            failures[:] = [t for t in failures if now-t < 900]
            if len(failures) >= 5:
                respond(handler, 429, {'ok':False,'message':'Too many attempts. Wait 15 minutes.'}); return True
            password = body.get('password')
            if not isinstance(password, str) or len(password)>256 or not check_password(password):
                failures.append(now); respond(handler, 401, {'ok':False,'message':'Admin login failed or has not been configured.'}); return True
            failures.clear()
            token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
            sessions.clear(); sessions[token]={'csrf':csrf,'expires':now+3600,'last_action':0}
        respond(handler, 200, {'ok':True,'csrf':csrf}, f'{COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=3600'); return True
    record=session(handler)
    csrf=handler.headers.get('X-CSRF-Token','')
    if not record or not hmac.compare_digest(csrf, record['csrf']):
        respond(handler, 403, {'ok':False,'message':'Admin login and CSRF token required.'}); return True
    if path == '/api/news-admin/logout':
        with lock: sessions.clear()
        respond(handler,200,{'ok':True},f'{COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0'); return True
    destination=body.get('destination')
    from news_destinations import reference, add_destination
    destination_ref=reference(database, destination) if destination else None
    if path in ('/api/news-admin/status','/api/news-admin/save','/api/news-admin/test') and not destination_ref:
        respond(handler,400,{'ok':False,'message':'Unknown destination.'}); return True
    if path in ('/api/news-admin/save','/api/news-admin/test','/api/news-admin/send','/api/news-admin/add-destination'):
        with lock:
            if now-record['last_action'] < 1:
                respond(handler,429,{'ok':False,'message':'Please wait before trying again.'}); return True
            record['last_action']=now
    try:
        if path == '/api/news-admin/add-destination':
            with lock:
                identifier=add_destination(database,body.get('name'),body.get('webhook'),validate_webhook)
            respond(handler,200,{'ok':True,'id':identifier,'message':'Destination and credential saved.'}); return True
        if path == '/api/news-admin/status':
            exists=credential_store.credential_exists(destination_ref)
            respond(handler,200,{'ok':True,'status':'Stored' if exists else 'Missing'}); return True
        if path == '/api/news-admin/save':
            secret=body.get('webhook')
            if not isinstance(secret,str): raise ValueError('Enter a Discord webhook URL.')
            validate_webhook(secret)
            with lock:
                exists=credential_store.credential_exists(destination_ref)
                if exists != (body.get('replace') is True):
                    respond(handler,409,{'ok':False,'message':'Credential state changed; check status and confirm replacement.'}); return True
                credential_store.save_credential(destination_ref,secret)
            respond(handler,200,{'ok':True,'message':'Credential stored in Windows Credential Manager.'}); return True
        if path == '/api/news-admin/test':
            secret=credential_store.get_credential(destination_ref,allow_environment=False)
            if not secret: respond(handler,404,{'ok':False,'message':'Credential is missing or unavailable.'}); return True
            ok,message=connection_test(secret)
            respond(handler,200 if ok else 502,{'ok':ok,'message':message}); return True
        if path == '/api/news-admin/send':
            if body.get('confirmed') is not True or not isinstance(body.get('id'),str) or not isinstance(body.get('payload'),dict) or not destination_ref:
                respond(handler,400,{'ok':False,'message':'Explicit confirmation and a valid preview are required.'}); return True
            store=DeliveryStore(database,lambda principal,csrf: principal is record and hmac.compare_digest(csrf,record['csrf']),format_publication,lambda dest:credential_store.get_credential(reference(database,dest),allow_environment=False),discord_transport)
            result=store.send(body['id'],record,csrf,confirmed=True,resend_confirmed=body.get('resend_confirmed') is True,expected_destination=body['destination'],expected_payload=body['payload'])
            respond(handler,200,{'ok':True,'result':result}); return True
        respond(handler,404,{'ok':False,'message':'Not found.'})
    except (ValueError, DeliveryError) as exc:
        # Validation and delivery errors are fixed local strings, never secret URLs.
        respond(handler,400,{'ok':False,'message':str(exc)})
    except Exception:
        respond(handler,503,{'ok':False,'message':'Operation failed. Check Windows Credential Manager and try again.'})
    return True
