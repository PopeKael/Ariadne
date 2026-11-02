"""Deliberate one-time YouTube feed baseline; never posts or schedules work."""
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from news_youtube_fallback import latest_video_pages

CHANNEL = re.compile(r'UC[A-Za-z0-9_-]{22}')
VIDEO = re.compile(r'[A-Za-z0-9_-]{11}')
def latest_video(channel_id):
    return latest_video_pages(channel_id)

def baseline(path, fetch=latest_video, source_id=None):
    if source_id is not None and not CHANNEL.fullmatch(str(source_id)):
        raise ValueError("Invalid source identifier.")
    path=Path(path)
    def connect():
        db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row;return db
    db=connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS news_checkpoints (source_id TEXT PRIMARY KEY, video_id TEXT NOT NULL, published_at TEXT NOT NULL, scanned_at TEXT NOT NULL)')
        if 'description' not in {row['name'] for row in db.execute('PRAGMA table_info(publications)')}:
            db.execute('ALTER TABLE publications ADD COLUMN description TEXT')
        db.commit()
        query="SELECT id,name,friendly_name,destination FROM sources WHERE enabled=1 AND kind='youtube'"
        if source_id is not None: query += " AND id=?"
        sources=[dict(row) for row in db.execute(query+" ORDER BY name", (source_id,) if source_id is not None else ())]
        if source_id is not None and not sources: raise ValueError("Enabled source not found.")
    finally:db.close()
    report={'baselined':[],'already_baselined':[],'failed':[]}
    for source in sources:
        db=connect()
        try: existing=db.execute('SELECT 1 FROM news_checkpoints WHERE source_id=?',(source['id'],)).fetchone()
        finally:db.close()
        if existing:report['already_baselined'].append(source['name']);continue
        try: video=fetch(source['id'])
        except Exception as exc:
            report['failed'].append({'source':source['name'],'reason':str(exc)[:200]});continue
        now=datetime.now(timezone.utc).isoformat()
        db=connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT name,friendly_name,destination,enabled FROM sources WHERE id=?',(source['id'],)).fetchone()
            if not current or not current['enabled']:
                db.rollback();report['failed'].append({'source':source['name'],'reason':'Source removed or disabled during scan'});continue
            if db.execute('SELECT 1 FROM news_checkpoints WHERE source_id=?',(source['id'],)).fetchone():
                db.rollback();report['already_baselined'].append(source['name']);continue
            vid=video['video_id']; url='https://www.youtube.com/watch?v='+vid
            # Existing publications and delivery status are never overwritten.
            db.execute('''INSERT OR IGNORE INTO publications(id,source_id,original_url,creator,title,published_at,summary,image_url,destination,collected_at,description)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',('youtube:'+vid,source['id'],url,current['friendly_name'] or current['name'],video['title'],video['published_at'],video['summary'],video['image_url'],current['destination'],now,video.get('description',video['summary'])))
            existing=db.execute('SELECT source_id,original_url FROM publications WHERE id=? OR (source_id=? AND original_url=?)',('youtube:'+vid,source['id'],url)).fetchone()
            if not existing or existing['source_id']!=source['id'] or existing['original_url']!=url:raise ValueError('Publication identity conflict')
            db.execute('INSERT INTO news_checkpoints VALUES(?,?,?,?)',(source['id'],vid,video['published_at'],now));db.commit()
            report['baselined'].append(source['name'])
        except Exception as exc:
            db.rollback();report['failed'].append({'source':source['name'],'reason':str(exc)[:200]})
        finally:db.close()
    return report
