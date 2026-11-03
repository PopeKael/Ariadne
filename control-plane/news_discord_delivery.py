from news_destinations import reference as destination_reference
"""Unwired manual-delivery state machine. No HTTP routes, credentials or network transport.

An authenticated adapter, secure secret lookup, shared-payload builder and Discord
transport must be supplied before integration. Until then this module is inert.
"""
import re
import math
import sqlite3
import time
import uuid
from pathlib import Path

DESTINATIONS=frozenset(('thailand_news','ai_noise'))

class DeliveryError(Exception): pass
class Denied(DeliveryError): pass
class Blocked(DeliveryError): pass

class DeliveryStore:
    def __init__(self,path,authorize,payload_builder,credential_lookup,transport,clock=time.time):
        if not all(callable(x) for x in (authorize,payload_builder,credential_lookup,transport,clock)):
            raise ValueError('Explicit authorization, formatter, credential and transport adapters are required')
        self.path=Path(path);self.authorize=authorize;self.payload_builder=payload_builder
        self.credential_lookup=credential_lookup;self.transport=transport;self.clock=clock

    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        return db

    def send(self,publication_id,principal,csrf_token,confirmed=False,resend_confirmed=False,expected_destination=None,expected_payload=None):
        # Both principal authentication and CSRF verification belong to the injected
        # trusted adapter; same-origin headers alone are not an authorization adapter.
        if not self.authorize(principal,csrf_token):raise Denied('Authorization required')
        if confirmed is not True:raise Denied('Explicit confirmation required')
        db=self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS discord_delivery_attempts (
              id TEXT PRIMARY KEY, publication_id TEXT NOT NULL, destination TEXT NOT NULL,
              started_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL,
              discord_message_id TEXT, retry_after REAL)''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(discord_delivery_attempts)')}
            for column,kind in (('http_status','INTEGER'),('discord_channel_id','TEXT'),('discord_timestamp','TEXT')):
                if column not in columns:db.execute(f'ALTER TABLE discord_delivery_attempts ADD COLUMN {column} {kind}')
            row=db.execute('SELECT * FROM publications WHERE id=?',(publication_id,)).fetchone()
            table='publications'
            if not row:
                row=db.execute('SELECT * FROM publication_archive WHERE id=?',(publication_id,)).fetchone()
                table='publication_archive'
            if not row:raise Blocked('Publication not found')
            publication=dict(row);destination=publication['destination']
            if destination not in DESTINATIONS and not destination_reference(self.path, destination):raise Blocked('Destination is not allowed')
            if expected_destination is not None and expected_destination!=destination:raise Blocked('Destination changed after preview; review it again')
            previous=db.execute('SELECT * FROM discord_delivery_attempts WHERE publication_id=? AND destination=? ORDER BY started_at DESC, rowid DESC LIMIT 1',(publication_id,destination)).fetchone()
            if previous and previous['status'] in ('inflight','uncertain'):
                raise Blocked('Previous delivery outcome requires reconciliation')
            sent_before=db.execute("SELECT 1 FROM discord_delivery_attempts WHERE publication_id=? AND destination=? AND status='sent' LIMIT 1",(publication_id,destination)).fetchone()
            if sent_before and resend_confirmed is not True:
                raise Blocked('Separate resend confirmation required')
            now=self.clock()
            # Conservative shared cooldown covers both webhook and global 429s.
            cooldown=db.execute("SELECT MAX(retry_after) FROM discord_delivery_attempts WHERE status='rate_limited'").fetchone()[0]
            if cooldown is not None and now<cooldown:raise Blocked('Discord rate limit is still active')
            recent=db.execute('SELECT started_at FROM discord_delivery_attempts ORDER BY started_at DESC LIMIT 1').fetchone()
            if recent and now-recent[0]<1:raise Blocked('Please wait before another attempt')
            secret=self.credential_lookup(destination)
            if not secret:raise Blocked('Destination credential unavailable')
            payload=self.payload_builder(publication)
            if expected_payload is not None and payload!=expected_payload:raise Blocked('Publication changed after preview; review it again')
            if payload.get('flags')!=32768 or payload.get('allowed_mentions')!={'parse':[]} or not payload.get('components') or any(k in payload for k in ('content','embeds','poll','files')):
                raise Blocked('Invalid Components V2 payload')
            attempt=uuid.uuid4().hex
            db.execute('INSERT INTO discord_delivery_attempts(id,publication_id,destination,started_at,status) VALUES(?,?,?,?,?)',(attempt,publication_id,destination,now,'inflight'))
            db.commit()
        except Exception:
            db.rollback();raise
        finally:db.close()
        # A timeout or ambiguous server error may have delivered: never auto-retry.
        status='uncertain';message_id=None;retry_after=None;code=None;discord_channel=None;discord_timestamp=None
        try:
            result=self.transport(secret,payload,{'wait':True,'with_components':True})
            code=result.get('status')
            if code==200 and re.fullmatch(r'[0-9]{1,25}',str(result.get('id') or '')):
                status='sent';message_id=str(result['id'])
                channel=str(result.get('channel_id') or '')
                if re.fullmatch(r'[0-9]{1,25}',channel):discord_channel=channel
                stamp=result.get('timestamp')
                if isinstance(stamp,str) and re.fullmatch(r'[0-9T:+.Z-]{10,40}',stamp):discord_timestamp=stamp
            elif code==429:
                delay=float(result.get('retry_after',60))
                if not math.isfinite(delay) or delay<0:delay=60.0
                status='rate_limited';retry_after=self.clock()+max(1.0,delay)
            elif isinstance(code,int) and 400<=code<500:
                status='rejected'
        except Exception:
            pass
        db=self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE discord_delivery_attempts SET status=?,finished_at=?,discord_message_id=?,retry_after=?,http_status=?,discord_channel_id=?,discord_timestamp=? WHERE id=?',(status,self.clock(),message_id,retry_after,code if isinstance(code,int) else None,discord_channel,discord_timestamp,attempt))
            if status=='sent':db.execute(f"UPDATE {table} SET discord_status='sent' WHERE id=?",(publication_id,))
            else:db.execute(f"UPDATE {table} SET discord_status=? WHERE id=? AND discord_status!='sent'",(status,publication_id))
            db.commit()
        finally:db.close()
        return {'status':status,'message_id':message_id,'attempt_id':attempt,'retry_after':retry_after}
