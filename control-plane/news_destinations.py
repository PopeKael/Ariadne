"""News Service destination registry and reversible-in-transaction schema upgrade."""
import re
import secrets
import sqlite3
import credential_store

BUILTINS={'thailand_news':('Thailand News','ARIADNE_DISCORD_THAILAND_NEWS'),
          'ai_noise':('AI Noise','ARIADNE_DISCORD_AI_NOISE')}
CHECK="CHECK(destination IN ('thailand_news','ai_noise'))"

def list_destinations(path):
    result=[{'id':key,'name':value[0]} for key,value in BUILTINS.items()]
    db=sqlite3.connect(path)
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_destinations'").fetchone():
            result.extend({'id':row[0],'name':row[1]} for row in db.execute('SELECT id,name FROM discord_destinations ORDER BY name COLLATE NOCASE'))
        return result
    finally:db.close()

def reference(path, destination):
    if not isinstance(destination,str):return None
    if destination in BUILTINS:return BUILTINS[destination][1]
    if not isinstance(destination,str) or not re.fullmatch(r'news_[a-f0-9]{24}',destination):return None
    db=sqlite3.connect(path)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_destinations'").fetchone():return None
        row=db.execute('SELECT credential_ref FROM discord_destinations WHERE id=?',(destination,)).fetchone()
        return row[0] if row else None
    finally:db.close()

def add_destination(path, name, webhook, validate):
    if not isinstance(name,str) or not 1<=len(name.strip())<=80 or any(ord(c)<32 for c in name):raise ValueError('Enter a destination name up to 80 characters.')
    name=name.strip();validate(webhook)
    if any(item['name'].casefold()==name.casefold() for item in list_destinations(path)):raise ValueError('A destination with that name already exists.')
    identifier='news_'+secrets.token_hex(12);ref='ARIADNE_NEWS_WEBHOOK_'+identifier[5:]
    db=sqlite3.connect(path,timeout=10)
    stored=False
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS discord_destinations (id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, credential_ref TEXT NOT NULL UNIQUE)')
        if db.execute('SELECT 1 FROM discord_destinations WHERE name=? COLLATE NOCASE',(name,)).fetchone():raise ValueError('A destination with that name already exists.')
        source_sql=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'").fetchone()[0]
        if CHECK not in source_sql and re.search(r'CHECK\s*\(\s*destination',source_sql,re.I):raise ValueError('Source schema requires manual review.')
        if CHECK in source_sql:
            objects=db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='sources' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='sources_destination_upgrade'").fetchone():raise ValueError('Source schema requires manual review.')
            new_sql=source_sql.replace('CREATE TABLE sources','CREATE TABLE sources_destination_upgrade',1).replace(CHECK,'',1)
            if new_sql==source_sql:raise ValueError('Source schema requires manual review.')
            db.execute(new_sql)
            columns=[r[1] for r in db.execute('PRAGMA table_info(sources)')]
            names=','.join('"'+c.replace('"','""')+'"' for c in columns)
            db.execute('INSERT INTO sources_destination_upgrade ('+names+') SELECT '+names+' FROM sources')
            db.execute('DROP TABLE sources');db.execute('ALTER TABLE sources_destination_upgrade RENAME TO sources')
            for (sql,) in objects:db.execute(sql)
            if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Destination migration failed its integrity check.')
        if credential_store.credential_exists(ref):raise ValueError('Credential collision; please retry.')
        credential_store.save_credential(ref,webhook);stored=True
        db.execute('INSERT INTO discord_destinations VALUES (?,?,?)',(identifier,name,ref))
        db.commit();stored=False
        return identifier
    except BaseException:
        db.rollback()
        if stored:credential_store.delete_credential(ref)
        raise
    finally:db.close()
