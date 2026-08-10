"""Authenticated-channel routing, daily budgets and concise upload reporting."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List
from .utils import setup_logger
logger=setup_logger(__name__)
@dataclass
class UploadSummary:
    attempted:int=0; uploaded:int=0; skipped_missing:int=0; skipped_unauthenticated:int=0; skipped_cap:int=0; failed:int=0
    channels:Dict[str,Dict[str,int]]=field(default_factory=dict)
    def text(self):
        return (f"UPLOAD_SUMMARY attempted={self.attempted} uploaded={self.uploaded} "
                f"missing={self.skipped_missing} unauthenticated={self.skipped_unauthenticated} "
                f"cap_skips={self.skipped_cap} failed={self.failed}")
def authenticated_channels(token_base:Path)->set:
    parent=token_base.parent
    return {p.name[len('youtube_token_ranking_'):-5] for p in parent.glob('youtube_token_ranking_*.json') if p.is_file()}
def route_channels(config, topic_channel:str, summary:UploadSummary):
    channels=[topic_channel] if topic_channel else []
    auth=authenticated_channels(Path(config.oauth_token_file))
    usable=[c for c in channels if c in auth]
    for c in channels: summary.channels.setdefault(c,{'uploaded':0,'remaining':0,'cap':int(config.upload_max_per_run or 1)})
    return usable, auth
def budget_available(db, channel:str, cap:int)->int:
    return max(0,cap-db.uploaded_count_for_channel_since(channel))
