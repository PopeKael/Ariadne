"""Persistent discovery pages stored in Core's atomic last-result snapshot.

No network or filesystem writes here: Core commits the cursor, pending metadata,
reserve cards and displayed page together only when a job completes successfully.
"""
from copy import deepcopy
from urllib.parse import urlsplit

PAGE_SIZE = 10
INSPECTION_BUDGET = 16


def name(card):
    return str(card.get('repository_name') or '').casefold()


def excluded_names(watches):
    # Saved decisions remain out of discovery, including paused/archived watches.
    result = set()
    for watch in watches:
        urls = list(watch.get('sources') or [])
        identity = str(watch.get('identity') or '')
        if identity.startswith('github:'):
            urls.append(identity[7:])
        for url in urls:
            parsed = urlsplit(str(url))
            parts = parsed.path.strip('/').split('/')
            if parsed.hostname == 'github.com' and len(parts) >= 2:
                result.add('/'.join(parts[:2]).removesuffix('.git').casefold())
    return result


def unique(cards, excluded=()):
    seen = set(excluded)
    result = []
    for card in cards:
        key = name(card)
        if key and key not in seen:
            seen.add(key)
            result.append(card)
    return result


def visible_result(result, excluded):
    from rabbit_assessment import rescore_cached
    view = deepcopy(result)
    deck = view.pop('_deck', {})
    pool = unique(view.get('results', []) + deck.get('reserve', []), excluded)
    pool = [rescore_cached(card) for card in pool]
    pool = [card for card in pool if card.get('assessment', {}).get('recommendation') != 'Ignore']
    view['results'] = pool[:PAGE_SIZE]
    view['needs_refill'] = len(pool) < PAGE_SIZE and (bool(deck.get('pending')) or not deck.get('exhausted', False))
    view['exhausted'] = bool(deck.get('exhausted')) and len(pool) <= PAGE_SIZE and not deck.get('pending')
    return view


def advance(config, search, enrich, report):
    from rabbit_assessment import rescore_cached
    previous = config.get('previous') or {}
    deck = deepcopy(previous.get('_deck') or {})
    excluded = set(config.get('excluded') or [])
    pool = [rescore_cached(card) for card in unique(previous.get('results', []) + deck.get('reserve', []), excluded)]
    current, reserve = pool[:PAGE_SIZE], pool[PAGE_SIZE:]
    reserve.sort(key=lambda c: -c.get('assessment', {}).get('selection_score', 0))
    seen = set(deck.get('seen') or []) | excluded
    # A refresh never calls this. Refill preserves the other cards; Next advances
    # past the displayed virtual page, including replacements from the reserve.
    if config.get('mode', 'explore') != 'refill':
        seen.update(name(c) for c in current)
        current = []
    pending = unique(deck.get('pending') or [], seen | {name(c) for c in current + reserve})
    page = int(deck.get('search_page') or 0)
    exhausted = bool(deck.get('exhausted'))
    warnings, inspected, queries = [], 0, 0
    matches = int(previous.get('selection', {}).get('search_matches') or 0)
    count = PAGE_SIZE - len(current)
    current += reserve[:count]
    reserve = reserve[count:]
    if len(current) < PAGE_SIZE:
        if not pending and not exhausted:
            batch = search(page + 1, report)
            warnings.extend(batch.get('warnings', []))
            queries = batch.get('queries_attempted', 0)
            if not batch.get('ok'):
                reason = warnings[0] if warnings else 'GitHub search was unavailable.'
                return dict(ok=False, results=[], warnings=warnings,
                            message='Discovery search failed; your previous page and browsing position were kept. ' + reason)
            page += 1
            matches = batch.get('search_matches', 0)
            exhausted = not batch.get('candidates') or page >= 33
            pending = unique(batch.get('candidates', []), seen | {name(c) for c in current + reserve})
        work, pending = pending[:INSPECTION_BUDGET], pending[INSPECTION_BUDGET:]
        if work:
            report('inspecting', f'Reading sources for {len(work)} unseen projects…', 85)
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix='rabbit-evidence') as executor:
                checked = list(executor.map(enrich, work))
            inspected = len(checked)
            accepted, failed = [], []
            for card in checked:
                assessment = card.get('assessment', {})
                if assessment.get('errors'):
                    failed.append(card)
                elif assessment.get('relevant') and assessment.get('recommendation') != 'Ignore':
                    accepted.append(card)
                else:
                    seen.add(name(card))
            accepted.sort(key=lambda c: (-c['assessment'].get('selection_score', 0), -c.get('score', 0), name(c)))
            reserve = unique(reserve + accepted, seen | {name(c) for c in current})
            count = PAGE_SIZE - len(current)
            current += reserve[:count]
            reserve = reserve[count:]
            pending = failed + pending
            if failed:
                detail = str(failed[0].get('assessment', {}).get('errors', [''])[0])
                warnings.append(f'{len(failed)} source inspections deferred; pending projects were retained. {detail}')
    if not current and warnings:
        return dict(ok=False, results=[], warnings=warnings,
                    message='No new cards passed source collection. Your previous page and browsing position were kept. ' + warnings[-1])
    from datetime import datetime, timezone
    result = dict(ok=True, completed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                  source='GitHub public REST repository search', queries_attempted=queries,
                  results=current, warnings=warnings,
                  selection=dict(search_matches=matches, inspected=inspected, shown=len(current)),
                  message=f'{len(current)} projects ready. ' + ('No more matches in this search.' if exhausted and not pending and not reserve else 'Continue with Next ten.'),
                  _deck=dict(seen=sorted(seen), reserve=reserve, pending=pending,
                             search_page=page, exhausted=exhausted))
    report('completed', result['message'], 100)
    return result
