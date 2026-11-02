"""Interactive Windows-only News Service delivery; no HTTP server or background work."""
import http.client
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import credential_store
from news_discord_delivery import DeliveryStore, Blocked, DeliveryError

ROOT=Path(__file__).resolve().parent
DATABASE=ROOT/'runtime'/'news-service.sqlite3'
REFERENCES={'thailand_news':'ARIADNE_DISCORD_THAILAND_NEWS','ai_noise':'ARIADNE_DISCORD_AI_NOISE'}
LABELS={'thailand_news':'Thailand News','ai_noise':'AI Noise'}

def validate_webhook(value):
    try:
        u=urlsplit(value.strip())
        if (u.scheme!='https' or u.hostname!='discord.com' or u.username or u.password or u.port
            or u.query or u.fragment or not re.fullmatch(r'/api/webhooks/[0-9]{10,25}/[A-Za-z0-9._-]{20,200}',u.path)):
            raise ValueError()
        return u.path
    except (ValueError,AttributeError):
        raise ValueError('Invalid Discord webhook URL; use the standard https://discord.com/api/webhooks/... format.') from None

def format_publication(publication):
    node=shutil.which('node')
    if not node:raise Blocked('Node.js is required to run the shared preview formatter.')
    script="const f=require(process.argv[1]);let s='';process.stdin.setEncoding('utf8');process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(f.build(JSON.parse(s)))));"
    try:
        result=subprocess.run([node,'-e',script,str(ROOT/'news-service.js')],input=json.dumps(publication,ensure_ascii=False),
          text=True,encoding='utf-8',capture_output=True,timeout=10,check=True,
          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return json.loads(result.stdout)
    except (OSError,subprocess.SubprocessError,ValueError):
        raise Blocked('Could not build the shared Discord preview.') from None

def discord_transport(secret,payload,options):
    if options!={'wait':True,'with_components':True}:raise ValueError('Discord confirmation options required')
    path=validate_webhook(secret)+'?wait=true&with_components=true'
    # http.client sends to this fixed HTTPS host and does not follow redirects.
    connection=http.client.HTTPSConnection('discord.com',timeout=20)
    try:
        encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        connection.request('POST',path,body=encoded,headers={'Content-Type':'application/json','User-Agent':'Ariadne-NewsService/1.0'})
        response=connection.getresponse();body=response.read(1_000_001)
        if len(body)>1_000_000:return {'status':response.status}
        try:parsed=json.loads(body.decode('utf-8'))
        except (ValueError,UnicodeDecodeError):parsed={}
        if not isinstance(parsed,dict):parsed={}
        result={'status':response.status}
        if response.status==200:
            result['id']=parsed.get('id');result['channel_id']=parsed.get('channel_id');result['timestamp']=parsed.get('timestamp')
        if response.status==429:
            retry=parsed.get('retry_after',response.getheader('Retry-After',60))
            try:result['retry_after']=float(retry)
            except (ValueError,TypeError):result['retry_after']=60
        return result
    finally:connection.close()

def list_publications(path=DATABASE):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:return [dict(row) for row in db.execute('SELECT * FROM publications ORDER BY published_at DESC,id DESC LIMIT 500')]
    finally:db.close()

def history(publication_id,path=DATABASE):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_delivery_attempts'").fetchone():return []
        return [dict(row) for row in db.execute('SELECT * FROM discord_delivery_attempts WHERE publication_id=? ORDER BY started_at DESC,rowid DESC',(publication_id,))]
    finally:db.close()

def show_history(rows):
    if not rows:print('Delivery history: none');return
    for row in rows:
        stamp=datetime.fromtimestamp(row['started_at'],timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
        print(f"{stamp} | {LABELS.get(row['destination'],'unknown destination')} | {row['status']} | Discord message ID: {row['discord_message_id'] or '-'} | Discord channel ID: {row.get('discord_channel_id') or '-'} | HTTP: {row.get('http_status') or '-'}")

def register():
    try:
        from news_discord_registration import open_registration
        open_registration(REFERENCES,LABELS,validate_webhook,credential_store.credential_exists,credential_store.save_credential)
    except Exception:
        print('Could not open the registration window. Check that Tkinter is installed and Windows desktop access is available.')

def preview(payload):
    print('\n--- Proposed Discord message ---')
    for component in payload['components']:
        if component['type']==12:print('Thumbnail:',component['items'][0]['media']['url'])
        elif component['type']==10:print(component['content'])
    print('--- End of preview ---')

def select_publication():
    rows=list_publications()
    if not rows:print('No cached publications.');return None
    for number,row in enumerate(rows,1):print(f"{number:3}. [{LABELS.get(row['destination'],'unsupported')}] {row['creator']} — {row['title']} | {row['discord_status']}")
    choice=input('Select a number (blank cancels): ').strip()
    if not choice.isdigit() or not 1<=int(choice)<=len(rows):return None
    return rows[int(choice)-1]

def send_interactive(row):
    destination=row['destination']
    if destination not in REFERENCES:print('Unsupported destination.');return
    entries=history(row['id']);show_history(entries)
    if entries and entries[0]['status'] in ('inflight','uncertain'):
        print('The latest delivery outcome is uncertain. Reconcile it in Discord before any further attempt.');return
    try:payload=format_publication(row)
    except Blocked as exc:print(str(exc));return
    print(f"\nTitle: {row['title']}\nDestination: {LABELS[destination]}")
    preview(payload)
    secret=credential_store.get_credential(REFERENCES[destination],allow_environment=False)
    if not secret:print('No credential saved for this destination.');return
    try:validate_webhook(secret)
    except ValueError:print('Saved credential is not a valid Discord webhook URL.');return
    resend=any(x['destination']==destination and x['status']=='sent' for x in entries)
    if resend and input('Previously sent. Type RESEND to confirm a deliberate duplicate: ').strip()!='RESEND':return
    if input(f'Type SEND to post this one publication to {LABELS[destination]} (blank cancels): ').strip()!='SEND':return
    # This command is interactive and local to the Windows user account; it is
    # not an HTTP authorization adapter. No principal or credential comes from a browser.
    principal=object()
    def checked_credential(dest):
        current=credential_store.get_credential(REFERENCES[dest],allow_environment=False)
        return current if current and hmac.compare_digest(current,secret) else ''
    service=DeliveryStore(DATABASE,lambda p,c:p is principal and c is principal,format_publication,
        checked_credential,discord_transport)
    try:
        result=service.send(row['id'],principal,principal,confirmed=True,resend_confirmed=resend,
                            expected_destination=destination,expected_payload=payload)
    except (DeliveryError,sqlite3.Error):print('Delivery was blocked or could not be recorded. Check history before retrying.');return
    print('Delivery outcome:',result['status'])
    if result['message_id']:print('Discord message ID:',result['message_id'])
    if result['status']=='uncertain':print('The outcome is unknown. Check Discord; do not retry blindly.')
    show_history(history(row['id']))

def main():
    if len(sys.argv)!=1:
        print('This command accepts no arguments. Never place webhook URLs on a command line.');return 1
    if os.name!='nt' or not sys.stdin.isatty() or not sys.stdout.isatty():
        print('Use an interactive Windows terminal under your own Windows account.');return 1
    while True:
        print('\nAriadne News Service — local Discord test\n1. Register or check webhook\n2. Preview and send a publication\n3. View a publication delivery history\n4. Exit')
        choice=input('Choose 1–4: ').strip()
        if choice=='1':register()
        elif choice in ('2','3'):
            row=select_publication()
            if row:
                if choice=='2':send_interactive(row)
                else:show_history(history(row['id']))
        elif choice in ('4','q','Q'):return 0

if __name__=='__main__':raise SystemExit(main())
