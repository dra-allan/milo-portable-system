"""YouTube Shorts uploader with OAuth and confirmed-upload cleanup."""
import os
from pathlib import Path
from typing import Optional,List,Dict
try:
 from .utils import setup_logger
 from .config import config
except ImportError:
 from utils import setup_logger
 from config import config
logger=setup_logger(__name__)
SCOPES=['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube','https://www.googleapis.com/auth/youtube.force-ssl']; DEFAULT_CATEGORY_ID='24'; SHORTS_HASHTAG='#Shorts'
def _build(credentials):
 from googleapiclient.discovery import build
 return build('youtube','v3',credentials=credentials,cache_discovery=False)
class YouTubeUploader:
 def __init__(self,channel:Optional[str]=None,credentials_path:Optional[str]=None,token_file:Optional[str]=None,privacy_status:Optional[str]=None):
  self.channel=channel; self.privacy_status=(privacy_status or config.privacy_status).lower(); self.credentials_path=Path(credentials_path or config.oauth_client_secrets or (config.project_root/'credentials.json')); base=Path(config.oauth_token_file); candidate=base.with_name(f'youtube_token_{channel}.json') if channel else base; self.token_file=Path(token_file) if token_file else (candidate if candidate.exists() else base); self.credentials=self._get_credentials(); self.youtube=_build(self.credentials)
 def _get_credentials(self):
  from google.auth.exceptions import RefreshError
  from google.auth.transport.requests import Request
  from google.oauth2.credentials import Credentials
  from google_auth_oauthlib.flow import InstalledAppFlow
  creds=None
  if self.token_file.exists():
   try:creds=Credentials.from_authorized_user_file(str(self.token_file),SCOPES)
   except (ValueError,OSError):creds=None
  if creds and creds.expired and creds.refresh_token:
   try:creds.refresh(Request())
   except RefreshError:creds=None
  if not creds or not creds.valid:
   if not self.credentials_path.exists():raise FileNotFoundError(f'OAuth client secrets not found at {self.credentials_path}')
   creds=InstalledAppFlow.from_client_secrets_file(str(self.credentials_path),SCOPES).run_local_server(port=0)
  self.token_file.parent.mkdir(parents=True,exist_ok=True);self.token_file.write_text(creds.to_json(),encoding='utf-8')
  try:os.chmod(self.token_file,0o600)
  except OSError:pass
  return creds
 def upload_short(self,video_path:str,title:str,description:str,tags:List[str],privacy_status:Optional[str]=None,category_id:str=DEFAULT_CATEGORY_ID,publish_at:Optional[str]=None)->Optional[str]:
  path=Path(video_path)
  if not path.exists():logger.error('UPLOAD_SKIP missing_file=%s',path);return None
  status=(privacy_status or self.privacy_status).lower();body={'snippet':{'title':title[:100],'description':description[:5000],'tags':[t for t in tags if t][:30],'categoryId':str(category_id),'defaultLanguage':'en'},'status':{'privacyStatus':status,'selfDeclaredMadeForKids':False}}
  if publish_at:body['status']['publishAt']=publish_at
  try:
   from googleapiclient.http import MediaFileUpload
   media=MediaFileUpload(str(path),chunksize=10*1024*1024,resumable=True);request=self.youtube.videos().insert(part='snippet,status',body=body,media_body=media);response=None
   while response is None:_,response=request.next_chunk()
   vid=response.get('id')
   if vid:
    try:path.unlink();logger.info('CLEANUP_DONE channel=%s path=%s',self.channel,path.name)
    except OSError as exc:logger.warning('CLEANUP_WARN channel=%s path=%s error=%s',self.channel,path.name,exc)
   logger.info('UPLOAD_DONE channel=%s video_id=%s privacy=%s',self.channel,vid,status);return vid
  except Exception as exc:logger.error('UPLOAD_FAIL channel=%s error=%s',self.channel,str(exc)[:240]);return None
 def get_video_details(self,video_id):
  try:return (self.youtube.videos().list(part='snippet,statistics,status',id=video_id).execute().get('items') or [None])[0]
  except Exception:return None
 def fetch_statistics(self,video_id):
  d=self.get_video_details(video_id);s=(d or {}).get('statistics') or {}
  def n(k):
   try:return int(s.get(k,0) or 0)
   except (TypeError,ValueError):return 0
  return {'views':n('viewCount'),'likes':n('likeCount'),'comments':n('commentCount'),'favorites':n('favoriteCount')} if d else None
 @staticmethod
 def auth_for_channel(channel,credentials_path=None,token_file=None):
  base=Path(config.oauth_token_file); token_file=token_file or str(base.with_name(f'youtube_token_{channel}.json')); up=YouTubeUploader(channel=channel,credentials_path=credentials_path,token_file=token_file,privacy_status='private'); items=up.youtube.channels().list(part='snippet',mine=True).execute().get('items') or [];return items[0].get('id') if items else None
