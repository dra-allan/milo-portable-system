"""Fast, bounded YouTube sourcing for ranking clips.

The important distinction here is discovery budget versus download budget.
A topic may discover many candidates, but it must not download all of them
just because vetting rejects the first batch. The default download budget is
small and explicit; raise it only for a topic with a proven pass rate.
"""
import re
from pathlib import Path
from typing import Dict,List,Optional
from .config import config
from .utils import ensure_dir,safe_slug,setup_logger
logger=setup_logger(__name__)

def _ydl(opts:Dict):
 from yt_dlp import YoutubeDL
 base={'quiet':True,'no_warnings':True,'noprogress':True,'ignoreerrors':True,'retries':3,'extractor_retries':3,'fragment_retries':8,'retry_sleep':lambda n:min(8,2**n),'socket_timeout':30,'concurrent_fragment_downloads':int(config.get('download_concurrency',4) or 4)};base.update(opts);return YoutubeDL(base)

def _youtube_target(value:str)->str:
 value=str(value or '').strip()
 if not value:return ''
 if value.startswith('@'):return f'https://www.youtube.com/{value}/videos'
 if value.startswith('UC') and '/' not in value:return f'https://www.youtube.com/channel/{value}/videos'
 return value

def _matches_negative(title:str,negatives:List[str])->Optional[str]:
 low=(title or '').lower()
 for negative in negatives:
  needle=(negative or '').strip().lower()
  if needle and re.search(r'(?<!\w)'+re.escape(needle)+r'(?!\w)',low):return needle
 return None

def _matches_required(title:str,required:List[str])->bool:
 low=(title or '').lower()
 return any((term or '').strip().lower() in low for term in required if term)

def _search_target(query:str,count:int)->str:
 return f'ytsearch{count}:{query}'

def _extract_search(ydl,query,count):
 target=_search_target(query,count)
 try:return ydl.extract_info(target,download=False) or {}
 except Exception as exc:
  text=str(exc)
  if 'Unsupported url scheme' not in text:raise
  logger.warning('YTDLP_SEARCH_FALLBACK query=%s reason=ytsearch unsupported',query)
  return ydl.extract_info(f'ytsearch{count}:{query}',download=False) or {}

def _candidate(entry:Dict,info:Dict,seen:set,topic_cfg:Dict,db)->Optional[Dict]:
 if not entry:return None
 url=entry.get('webpage_url') or entry.get('url') or ''
 vid=entry.get('id') or ''
 if url and not url.startswith('http'):url=f'https://www.youtube.com/watch?v={vid}'
 if not url or 'youtube.com' not in url or url in seen:return None
 seen.add(url)
 title=entry.get('title') or ''
 if db.is_used(url) or db.is_rejected(url):return None
 hit=_matches_negative(title,topic_cfg.get('negative_keywords') or [])
 if hit:
  db.mark_rejected(url,topic_cfg['name'],f'negative:{hit}');return None
 required=topic_cfg.get('require_keywords') or []
 if required and not _matches_required(title,required):
  db.mark_rejected(url,topic_cfg['name'],'off_topic');return None
 duration=float(entry.get('duration') or 0)
 views=int(entry.get('view_count') or 0)
 max_duration=float(config.get('max_source_duration',900))
 min_views=int(config.get('min_source_views',500))
 if duration and duration>max_duration:
  db.mark_rejected(url,topic_cfg['name'],'too_long');return None
 if views and views<min_views:return None
 return {'url':url,'source_id':vid,'title':title,'duration':duration,'views':views,'uploader':entry.get('uploader') or entry.get('channel') or '','extractor':entry.get('ie_key') or info.get('extractor') or ''}

def discover(topic_cfg:Dict,db,limit:Optional[int]=None)->List[Dict]:
 limit=limit or int(config.get('candidates_per_topic',40))
 # Discovery can be broad; downloading is intentionally bounded below.
 download_budget=int(topic_cfg.get('max_download_attempts') or config.get('max_download_attempts',8))
 limit=max(limit,download_budget)
 queries=topic_cfg.get('queries') or []
 per_query=max(5,limit//max(1,len(queries)))
 found=[];seen=set()
 with _ydl({'extract_flat':'in_playlist','skip_download':True,'playlistend':per_query}) as ydl:
  for query in queries:
   try:info=_extract_search(ydl,query,per_query)
   except Exception as exc:logger.warning('SOURCE_LIST_FAIL query=%s error=%s',query,str(exc)[:180]);continue
   for entry in (info.get('entries') or []):
    item=_candidate(entry,info,seen,topic_cfg,db)
    if item:found.append(item)
    if len(found)>=limit:break
   if len(found)>=limit:break
  if len(found)<limit:
   for channel in (topic_cfg.get('channels') or []):
    target=_youtube_target(channel)
    if not target:continue
    try:info=ydl.extract_info(target,download=False) or {}
    except Exception as exc:logger.warning('SOURCE_CHANNEL_FAIL channel=%s error=%s',channel,str(exc)[:180]);continue
    for entry in (info.get('entries') or []):
     item=_candidate(entry,info,seen,topic_cfg,db)
     if item:
      item['uploader']=channel;item['extractor']='youtube';found.append(item)
     if len(found)>=limit:break
    if len(found)>=limit:break
 logger.info('SOURCE_LIST_DONE candidates=%d download_budget=%d',len(found),download_budget)
 return found[:download_budget]

def download(candidate:Dict,dest_dir:Optional[Path]=None)->Optional[Path]:
 dest_dir=ensure_dir(dest_dir or config.clips_dir)
 stem=safe_slug(f"{candidate.get('source_id') or ''}_{candidate.get('title') or 'clip'}")
 template=str(dest_dir/f'{stem}.%(ext)s')
 # Vetting does not need 720p. A 480p proxy cuts disk and decode cost sharply;
 # accepted clips are still perfectly usable as vertical Shorts.
 height=int(config.get('max_download_height',480) or 480)
 max_bytes=int(config.get('max_download_bytes',100*1024*1024) or 0)
 opts={'outtmpl':template,'format':f'bestvideo[height<={height}][vcodec!=av01]+bestaudio/bestvideo[height<={height}]+bestaudio/best[height<={height}]/best','format_sort':['res','vbr','abr'],'merge_output_format':'mp4','noplaylist':True,'max_filesize':max_bytes if max_bytes>0 else None,'continuedl':True,'keepvideo':True,'restrictfilenames':True}
 try:
  with _ydl(opts) as ydl:info=ydl.extract_info(candidate['url'],download=True)
 except Exception as exc:logger.warning('SOURCE_DOWNLOAD_FAIL id=%s error=%s',candidate.get('source_id'),str(exc)[:220]);return None
 if not info:return None
 for path in sorted(dest_dir.glob(f'{stem}.*')):
  if path.suffix.lower() in ('.mp4','.mkv','.webm','.mov') and path.stat().st_size>64*1024:
   candidate['local_path']=str(path);candidate['title']=info.get('title') or candidate.get('title');logger.info('SOURCE_READY id=%s size_mb=%.1f height_cap=%d',candidate.get('source_id'),path.stat().st_size/1048576,height);return path
 logger.warning('SOURCE_DOWNLOAD_EMPTY id=%s',candidate.get('source_id'));return None
