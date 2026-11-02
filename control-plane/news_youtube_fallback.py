"""Bounded, on-demand public YouTube page fallback; no scheduler or credentials."""
import json
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

CHANNEL=re.compile(r'UC[A-Za-z0-9_-]{22}')
VIDEO=re.compile(r'[A-Za-z0-9_-]{11}')

def _page(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept-Language':'en-US,en;q=0.9'})
    with urllib.request.urlopen(request,timeout=15) as response:
        target=urlsplit(response.geturl())
        if target.scheme!='https' or target.hostname not in {'youtube.com','www.youtube.com','m.youtube.com'}:
            raise ValueError('Unexpected YouTube redirect')
        raw=response.read(6_000_001)
    if len(raw)>6_000_000:raise ValueError('YouTube page exceeded size limit')
    return raw.decode('utf-8','replace')

def _json(page, variable):
    match=re.search(r'var\s+'+re.escape(variable)+r'\s*=\s*',page)
    if not match:raise ValueError('YouTube did not provide '+variable)
    try:return json.JSONDecoder().raw_decode(page[match.end():])[0]
    except ValueError as exc:raise ValueError('Invalid YouTube response data') from exc

def _tabs(channel_id, tab):
    data=_json(_page('https://www.youtube.com/channel/'+channel_id+'/'+tab),'ytInitialData')
    if data.get('metadata',{}).get('channelMetadataRenderer',{}).get('externalId')!=channel_id:
        raise ValueError(tab+': channel identity mismatch')
    tabs=data.get('contents',{}).get('twoColumnBrowseResultsRenderer',{}).get('tabs',[])
    renderers=[x.get('tabRenderer',{}) for x in tabs if isinstance(x,dict)]
    def url(t):return t.get('endpoint',{}).get('commandMetadata',{}).get('webCommandMetadata',{}).get('url','').split('?')[0].rstrip('/')
    available=[t for t in renderers if url(t).endswith('/'+tab)]
    if not available:return []
    selected=[t for t in available if t.get('selected')]
    if not selected:raise ValueError(tab+': requested tab was not selected')
    grid=selected[0].get('content',{}).get('richGridRenderer')
    if not isinstance(grid,dict):raise ValueError(tab+': video list unavailable')
    ids=[]
    for row in grid.get('contents',[]):
        content=row.get('richItemRenderer',{}).get('content',{})
        lockup=content.get('lockupViewModel',{})
        vid=lockup.get('contentId') if lockup.get('contentType')=='LOCKUP_CONTENT_TYPE_VIDEO' else None
        if not vid:vid=content.get('videoRenderer',{}).get('videoId')
        if not vid:vid=content.get('shortsLockupViewModel',{}).get('onTap',{}).get('innertubeCommand',{}).get('reelWatchEndpoint',{}).get('videoId')
        if vid and VIDEO.fullmatch(vid) and vid not in ids:ids.append(vid)
    if not ids and grid.get('contents'):
        raise ValueError(tab+': video list format was not recognized')
    return ids

def _video(channel_id, video_id):
    player=_json(_page('https://www.youtube.com/watch?v='+video_id),'ytInitialPlayerResponse')
    details=player.get('videoDetails',{}); meta=player.get('microformat',{}).get('playerMicroformatRenderer',{})
    if player.get('playabilityStatus',{}).get('status')!='OK':return None
    if details.get('channelId')!=channel_id or details.get('videoId')!=video_id:raise ValueError('Video identity mismatch')
    live=meta.get('liveBroadcastDetails') or {}
    if details.get('isPrivate') is not False or meta.get('isUnlisted') is not False:return None
    if details.get('isUpcoming') or details.get('isLive') or live.get('isLiveNow'):return None
    timestamp=meta.get('publishDate') or ''
    try:
        published=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
        if published.tzinfo is None:raise ValueError()
    except ValueError as exc:raise ValueError('Publication timestamp unavailable') from exc
    if published>datetime.now(timezone.utc):return None
    title=details.get('title')
    if not isinstance(title,str) or not title.strip() or '\ufffd' in title:raise ValueError('Video title unavailable or contains invalid text')
    description=details.get('shortDescription') or ''
    return {'video_id':video_id,'title':title.strip(),'summary':str(description)[:1500],'description':str(description),
            'image_url':'https://i.ytimg.com/vi/'+video_id+'/hqdefault.jpg',
            'published_at':published.astimezone(timezone.utc).isoformat()}

def latest_video_pages(channel_id):
    if not CHANNEL.fullmatch(channel_id):raise ValueError('Invalid canonical channel ID')
    candidates=[]
    for tab in ('videos','shorts','streams'):
        ids=_tabs(channel_id,tab)
        if not ids:continue
        found=None
        for video_id in ids[:5]:
            item=_video(channel_id,video_id)
            if item:
                found=item;break
        if not found:raise ValueError(tab+': no published, accessible video in first five items')
        candidates.append(found)
    if not candidates:raise ValueError('No public uploaded videos found')
    return max(candidates,key=lambda item:item['published_at'])
