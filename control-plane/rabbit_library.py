"""Local undecided-project library. Browsing and decisions never call GitHub."""
from contextlib import contextmanager
import json
import hashlib
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

PAGE_SIZE = 9


def repository_name(value):
    text = str(value or '').strip()
    parsed = urlsplit(text)
    if parsed.scheme:
        if parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.username or parsed.password or parsed.port:
            raise ValueError('Use an https://github.com/owner/project link.')
        parts = parsed.path.strip('/').split('/')
        if len(parts) != 2 or parsed.query or parsed.fragment:
            raise ValueError('Use the repository link, without an issue, branch or query.')
        text = '/'.join(parts)
    text = text.removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*', text):
        raise ValueError('Provide a GitHub repository as owner/project or its HTTPS link.')
    return text


class RabbitLibrary:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS cards (
                      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                      data TEXT NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0,
                      decision_order INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER);
                ''')
                yield db
        finally:
            db.close()

    def add(self, cards, *, focus=False, excluded=(), update=True):
        with self.db() as db:
            first = None
            for card in cards:
                key = repository_name(card.get('repository_name')).casefold()
                existing = db.execute('SELECT id FROM cards WHERE name=?', (key,)).fetchone()
                # Rediscovery updates evidence but never reverses a dismissal.
                if update or not existing:
                    db.execute('INSERT INTO cards(name,data) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data',
                               (key, json.dumps(card, ensure_ascii=False)))
                if first is None and (focus or not existing):
                    first = key
            if focus and first:
                rows = [r['name'] for r in db.execute('SELECT name FROM cards WHERE dismissed=0 ORDER BY id') if r['name'] not in excluded]
                if first in rows:
                    self._cursor(db, rows.index(first) // PAGE_SIZE)

    @staticmethod
    def _cursor(db, value):
        db.execute("INSERT INTO meta VALUES('page',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))

    def view(self, excluded=(), move=None):
        from rabbit_assessment import rescore_cached
        excluded = set(excluded)
        with self.db() as db:
            rows = [r for r in db.execute('SELECT name,data FROM cards WHERE dismissed=0 ORDER BY id') if r['name'] not in excluded]
            pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
            saved = db.execute("SELECT value FROM meta WHERE key='page'").fetchone()
            page = min(max(0, saved['value'] if saved else 0), pages - 1)
            if move == 'next': page = (page + 1) % pages
            elif move == 'previous': page = (page - 1) % pages
            elif move == 'first': page = 0
            elif move is not None: raise ValueError('Unknown browsing direction.')
            self._cursor(db, page)
            last = db.execute('SELECT name,data FROM cards WHERE dismissed=1 ORDER BY decision_order DESC LIMIT 1').fetchone()
            dismissed = db.execute('SELECT COUNT(*) FROM cards WHERE dismissed=1').fetchone()[0]
            return dict(results=[rescore_cached(json.loads(r['data'])) for r in rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]],
                        page=page + 1, pages=pages, total=len(rows), page_size=PAGE_SIZE, dismissed_count=dismissed,
                        undo=dict(repository_name=last['name'], title=json.loads(last['data'])['repository_name']) if last else None)

    def decide(self, repository, action):
        key = repository_name(repository).casefold()
        if action not in {'dismiss', 'undo'}: raise ValueError('Unknown project decision.')
        with self.db() as db:
            row = db.execute('SELECT id FROM cards WHERE name=?', (key,)).fetchone()
            if not row: raise ValueError('That project is not in the library.')
            order = db.execute('SELECT COALESCE(MAX(decision_order),0)+1 FROM cards').fetchone()[0]
            db.execute('UPDATE cards SET dismissed=?, decision_order=? WHERE name=?', (int(action == 'dismiss'), order, key))

    def dismissed_names(self):
        with self.db() as db:
            return {r['name'] for r in db.execute('SELECT name FROM cards WHERE dismissed=1')}

    def recover_legacy(self, result, cache_path):
        """Recover seen projects from surviving evidence; label missing metadata."""
        from rabbit_assessment import rescore_cached, CACHE_ROOT
        with self.db() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='legacy_recovered_v2'").fetchone(): return
        wanted = set(result.get('_deck', {}).get('seen', []))
        recovered = []
        if wanted and Path(cache_path).exists():
            cache = sqlite3.connect(f'{Path(cache_path).as_uri()}?mode=ro', uri=True)
            try:
                for (body,) in cache.execute('SELECT body FROM cache'):
                    try:
                        payload = json.loads(body)
                        items = payload.get('items', []) if isinstance(payload, dict) else []
                        for item in items:
                            key = str(item.get('full_name', '')).casefold()
                            if key not in wanted: continue
                            card = dict(repository_name=item['full_name'], github_url='https://github.com/' + item['full_name'],
                                        description=item.get('description') or 'Recovered from earlier discovery.',
                                        language=item.get('language') or 'Not stated', topics=item.get('topics') or [],
                                        metrics={k:item.get(k) for k in ('created_at','pushed_at','updated_at','stargazers_count','forks_count','open_issues_count','size','archived','disabled')})
                            card = rescore_cached(card)
                            assessment = card.get('assessment', {})
                            if assessment.get('relevant') and assessment.get('recommendation') != 'Ignore' and not assessment.get('errors'):
                                recovered.append(card)
                    except (ValueError, TypeError, KeyError): continue
            except sqlite3.OperationalError:
                pass
            finally:
                cache.close()
        recovered_names = {c['repository_name'].casefold() for c in recovered}
        for key in sorted(wanted - recovered_names):
            try:
                saved = json.loads((CACHE_ROOT / (hashlib.sha256(key.encode()).hexdigest() + '.json')).read_text(encoding='utf-8'))
                signature = saved.get('signature') or [None, None]
                card = dict(repository_name=repository_name(key), github_url='https://github.com/' + key,
                            description='Recovered from saved source evidence. Full repository metadata needs refreshing.',
                            recovered=True, language='Not supplied', topics=[],
                            metrics=dict(pushed_at=signature[0], updated_at=signature[1]))
                card = rescore_cached(card)
                assessment = card.get('assessment', {})
                if assessment.get('relevant') and assessment.get('recommendation') != 'Ignore' and not assessment.get('errors'):
                    recovered.append(card)
            except (OSError, ValueError, TypeError, KeyError, IndexError): continue
        self.add(recovered, update=False)
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO meta VALUES('legacy_recovered_v2',1)")
