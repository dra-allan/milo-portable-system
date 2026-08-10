"""Namespaced OAuth publisher with post-confirmation local cleanup."""
import os
from pathlib import Path
from typing import List, Optional
from .config import config
from .utils import setup_logger
logger=setup_logger(__name__)
SCOPES=['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube']; DEFAULT_CATEGORY_ID='24'
class RankingPublisher:
    def __init__(self,channel:Optional[str]=None,privacy_status:Optional[str]=None):
        self.channel=channel; self.privacy_status=(privacy_status or config.privacy_status).lower(); self.credentials_path=Path(config.oauth_client_secrets); base=Path(config.oauth_token_file); self.token_file=base.with_name(f'youtube_token_ranking_{channel}.json') if channel else base; self.credentials=self._credentials(); from googleapiclient.discovery import build; self.youtube=build('youtube','v3',credentials=self.credentials,cache_discovery=False)
    def _credentials(self):
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
    def upload(self,video_path:str,title:str,description:str,tags:List[str],privacy_status:Optional[str]=None)->Optional[str]:
        path=Path(video_path)
        if not path.exists():logger.error('UPLOAD_SKIP missing_file=%s',video_path);return None
        status=(privacy_status or self.privacy_status).lower();body={'snippet':{'title':title[:100],'description':description[:5000],'tags':[t for t in tags if t][:30],'categoryId':DEFAULT_CATEGORY_ID,'defaultLanguage':'en'},'status':{'privacyStatus':status,'selfDeclaredMadeForKids':False}}
        try:
            from googleapiclient.http import MediaFileUpload
            media=MediaFileUpload(str(path),chunksize=10*1024*1024,resumable=True);request=self.youtube.videos().insert(part='snippet,status',body=body,media_body=media);response=None
            while response is None:_,response=request.next_chunk()
            vid=response.get('id')
            if vid:
                try:path.unlink();logger.info('CLEANUP_DONE path=%s',path.name)
                except OSError as exc:logger.warning('CLEANUP_WARN path=%s error=%s',path.name,exc)
            logger.info('UPLOAD_DONE channel=%s video_id=%s privacy=%s',self.channel,vid,status);return vid
        except Exception as exc:logger.error('UPLOAD_FAIL channel=%s error=%s',self.channel,str(exc)[:240]);return None
    def channel_id(self):
        try:
            items=self.youtube.channels().list(part='snippet',mine=True).execute().get('items') or [];return items[0]['id'] if items else None
        except Exception:return None
def auth(channel:str)->Optional[str]:return RankingPublisher(channel=channel,privacy_status='private').channel_id()
