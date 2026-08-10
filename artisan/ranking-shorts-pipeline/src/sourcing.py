"""Fast, bounded YouTube sourcing for ranking clips."""
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
def _search_target(query:str,count:int)->str:
 """yt-dlp search scheme; ytsearchdate[N]: is a valid scheme."""
 return f'ytsearch{count}:{query}'
def _extract_search(ydl,query,count):
 target=_search_target(query,count)
 try:return ydl.extract_info(target,download=False) or {}
 except Exception as exc:
  text=str(exc)
  if 'Unsupported url scheme' not in text:raise
  logger.warning('YTDLP_SEARCH_FALLBACK query=%s reason=ytsearch unsupported',query)
  return ydl.extract_info(f'ytsearch{count}:{query}',download=False) or {}
def discover(topic_cfg:Dict,db,limit:Optional[int]=None)->List[Dict]:
 limit=limit or int(config.get('candidates_per_topic',40));max_duration=float(config.get('max_source_duration',900));min_views=int(config.get('min_source_views',500));queries=topic_cfg.get('queries') or [];per_query=max(5,limit//max(1,len(queries)));found=[];seen=set()
 with _ydl({'extract_flat':'in_playlist','skip_download':True,'playlistend':per_query}) as ydl:
  for query in queries:
   try:info=_extract_search(ydl,query,per_query)
   except Exception as exc:logger.warning('SOURCE_LIST_FAIL query=%s error=%s',query,str(exc)[:180]);continue
   for entry in (info.get('entries') or []):
    if not entry:continue
    url=entry.get('webpage_url') or entry.get('url') or ''
    if url and not url.startswith('http'):url=f"https://www.youtube.com/watch?v={entry.get('id')}"
    if not url or url in seen or 'youtube.com' not in url:continue
    seen.add(url);title=entry.get('title') or '';duration=float(entry.get('duration') or 0);views=int(entry.get('view_count') or 0)
    if db.is_used(url) or db.is_rejected(url):continue
    hit=_matches_negative(title,topic_cfg.get('negative_keywords') or [])
    if hit:db.mark_rejected(url,topic_cfg['name'],f'negative:{hit}');continue
    if duration and duration>max_duration:db.mark_rejected(url,topic_cfg['name'],'too_long');continue
    if views and views<min_views:continue
    found.append({'url':url,'source_id':entry.get('id') or '','title':title,'duration':duration,'views':views,'uploader':entry.get('uploader') or entry.get('channel') or '','extractor':entry.get('ie_key') or info.get('extractor') or ''})
    if len(found)>=limit:return found
  for channel in (topic_cfg.get('channels') or []):
   target=_youtube_target(channel)
   if not target:continue
   try:info=ydl.extract_info(target,download=False) or {}
   except Exception as exc:logger.warning('SOURCE_CHANNEL_FAIL channel=%s error=%s',channel,str(exc)[:180]);continue
   for entry in (info.get('entries') or []):
    if not entry:continue
    vid=entry.get('id') or '';url=entry.get('webpage_url') or f'https://www.youtube.com/watch?v={vid}'
    if not vid or url in seen:continue
    seen.add(url);title=entry.get('title') or '';duration=float(entry.get('duration') or 0);views=int(entry.get('view_count') or 0)
    if db.is_used(url) or db.is_rejected(url):continue
    hit=_matches_negative(title,topic_cfg.get('negative_keywords') or [])
    if hit or (duration and duration>max_duration) or (views and views<min_views):continue
    found.append({'url':url,'source_id':vid,'title':title,'duration':duration,'views':views,'uploader':channel,'extractor':'youtube'})
    if len(found)>=limit:return found
 logger.info('SOURCE_LIST_DONE candidates=%d',len(found));return found
def download(candidate:Dict,dest_dir:Optional[Path]=None)->Optional[Path]:
 dest_dir=ensure_dir(dest_dir or config.clips_dir);stem=safe_slug(f"{candidate.get('source_id') or ''}_{candidate.get('title') or 'clip'}");template=str(dest_dir/f'{stem}.%(ext)s');height=int(config.get('max_download_height',720) or 720);max_bytes=int(config.get('max_download_bytes',250*1024*1024) or 0);opts={'outtmpl':template,'format':f'bestvideo[height<={height}][vcodec!=av01]+bestaudio/bestvideo[height<={height}]+bestaudio/best[height<={height}]/best','format_sort':['res','vbr','abr'],'merge_output_format':'mp4','noplaylist':True,'max_filesize':max_bytes if max_bytes>0 else None,'continuedl':True,'keepvideo':True,'restrictfilenames':True}
 try:
  with _ydl(opts) as ydl:info=ydl.extract_info(candidate['url'],download=True)
 except Exception as exc:logger.warning('SOURCE_DOWNLOAD_FAIL id=%s error=%s',candidate.get('source_id'),str(exc)[:220]);return None
 if not info:return None
 for path in sorted(dest_dir.glob(f'{stem}.*')):
  if path.suffix.lower() in ('.mp4','.mkv','.webm','.mov') and path.stat().st_size>64*1024:
   candidate['local_path']=str(path);candidate['title']=info.get('title') or candidate.get('title');logger.info('SOURCE_READY id=%s size_mb=%.1f height_cap=%d',candidate.get('source_id'),path.stat().st_size/1048576,height);return path
 logger.warning('SOURCE_DOWNLOAD_EMPTY id=%s',candidate.get('source_id'));return None
