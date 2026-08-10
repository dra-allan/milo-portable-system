"""Ranking pipeline orchestration with safe multi-channel publishing."""
import argparse, json, os, sys, time
from pathlib import Path
from typing import Dict, List, Optional
from . import assembler, ranker, scriptwriter, sourcing, vetting
from .config import config
from .database import RankingDatabase
from .upload_policy import UploadSummary, authenticated_channels, budget_available
from .utils import ensure_dir, safe_slug, setup_logger, which_ffmpeg
logger=setup_logger(__name__, config.log_dir/'ranking.log')

class RankingPipeline:
    def __init__(self):
        self.db=RankingDatabase(config.db_path); self.stats={'built':0,'uploaded':0,'errors':0}; self.summary=UploadSummary()
    def collect_clips(self, topic_cfg, needed):
        candidates=sourcing.discover(topic_cfg,self.db); known=self.db.known_hashes(); accepted=[]; rejects=0
        for candidate in candidates:
            if len(accepted)>=needed or rejects>=max(12,needed*4): break
            path=sourcing.download(candidate)
            if not path: rejects+=1; self.db.mark_rejected(candidate['url'],topic_cfg['name'],'download_failed'); continue
            result=vetting.vet(candidate,known)
            if not result.get('ok'):
                rejects+=1; self.db.mark_rejected(candidate['url'],topic_cfg['name'],result.get('reason') or 'rejected'); Path(path).unlink(missing_ok=True); continue
            if result.get('phash'): known.append(result['phash'])
            accepted.append(result)
        logger.info('COLLECT topic=%s accepted=%d rejected=%d',topic_cfg['name'],len(accepted),rejects)
        return accepted
    def build(self, topic_name, upload=True):
        topic_cfg=config.topic(topic_name); needed=int(config.get('clips_per_video',5))
        clips=self.collect_clips(topic_cfg,needed)
        if len(clips)<2: logger.error('BUILD_FAIL topic=%s reason=not_enough_clips count=%d',topic_name,len(clips)); self.stats['errors']+=1; return None
        ordered=ranker.rank(clips,count=len(clips)); ordered[0]['hook_candidate']=True
        meta=scriptwriter.write_copy(topic_cfg,ordered); slug=f'{topic_name}_{int(time.time())}'
        scriptwriter.generate_voiceover(ordered,slug); scriptwriter.attach_sfx(ordered)
        plan={'topic':topic_name,'slug':slug,'video_title':meta['video_title'],'upload_title':meta['upload_title'],'description':meta['description'],'tags':meta['tags'],'channel':topic_cfg.get('channel'),'clips':[{'path':c['local_path'],'start':c.get('clip_start',0.0),'duration':c.get('clip_duration',4.0),'rank':c['rank'],'title':c.get('title'),'vo_path':c.get('vo_path'),'sfx':c.get('sfx') or [],'text_boxes':c.get('text_boxes') or [],'url':c.get('url'),'uploader':c.get('uploader'),'phash':c.get('phash'),'score':c.get('score')} for c in ordered]}
        self._save_plan(plan)
        if config.dry_run: return plan
        output=assembler.assemble(plan)
        if not output: self.stats['errors']+=1; logger.error('BUILD_FAIL topic=%s reason=assembly',topic_name); return None
        build_id=self.db.record_build(topic_name,meta['upload_title'],str(output),plan)
        for c in plan['clips']: self.db.mark_used(c['url'],topic_name,c.get('phash'),c.get('title'))
        self.db.touch_topic(topic_name); plan.update(local_path=str(output),build_id=build_id); self.stats['built']+=1
        logger.info('BUILD_DONE topic=%s path=%s',topic_name,output)
        if upload: self.upload_build(build_id,plan)
        return plan
    def _channel_for_plan(self,plan): return plan.get('channel') or ''
    def upload_build(self,build_id,plan):
        channel=self._channel_for_plan(plan); cap=int(os.getenv('UPLOAD_MAX_PER_CHANNEL','6'))
        self.summary.attempted+=1
        if not channel:
            self.summary.failed+=1; self.db.mark_failed(build_id,'no_channel'); logger.error('UPLOAD_SKIP reason=no_channel build=%s',build_id); return None
        auth=authenticated_channels(Path(config.oauth_token_file))
        if channel not in auth:
            self.summary.skipped_unauthenticated+=1; self.db.mark_failed(build_id,'unauthenticated'); logger.warning('UPLOAD_SKIP channel=%s reason=unauthenticated',channel); return None
        remaining=budget_available(self.db,channel,cap); self.summary.channels.setdefault(channel,{'uploaded':0,'remaining':remaining,'cap':cap})
        if remaining<=0:
            self.summary.skipped_cap+=1; logger.info('UPLOAD_SKIP channel=%s reason=daily_cap used=%d cap=%d',channel,cap,cap); return None
        try:
            from .publisher import RankingPublisher
            publisher=RankingPublisher(channel=channel)
            video_id=publisher.upload(plan['local_path'],plan['upload_title'],plan.get('description',''),plan.get('tags') or [])
        except Exception as exc:
            self.summary.failed+=1; self.stats['errors']+=1; self.db.mark_failed(build_id,'publisher_error'); logger.error('UPLOAD_FAIL channel=%s error=%s',channel,str(exc)[:240]); return None
        if not video_id:
            self.summary.failed+=1; self.stats['errors']+=1; self.db.mark_failed(build_id,'upload_failed'); logger.error('UPLOAD_FAIL channel=%s reason=no_video_id',channel); return None
        self.db.mark_uploaded(build_id,video_id,channel); self.summary.uploaded+=1; self.stats['uploaded']+=1; self.summary.channels[channel]['uploaded']+=1; self.summary.channels[channel]['remaining']=max(0,self.summary.channels[channel]['remaining']-1); logger.info('UPLOAD_DONE channel=%s video_id=%s build=%s',channel,video_id,build_id); return video_id
    def drain_queue(self):
        rows=self.db.pending_builds(limit=1000); auth=authenticated_channels(Path(config.oauth_token_file)); cap=int(os.getenv('UPLOAD_MAX_PER_CHANNEL','6')); available={c:budget_available(self.db,c,cap) for c in auth}; uploaded=0
        for row in rows:
            path=row.get('local_path')
            if not path or not Path(path).exists(): self.db.mark_failed(row['id'],'file_missing'); self.summary.skipped_missing+=1; logger.warning('UPLOAD_SKIP build=%s reason=file_missing path=%s',row['id'],path); continue
            plan=json.loads(row.get('plan_json') or '{}'); channel=plan.get('channel') or row.get('upload_channel') or ''
            if not channel or channel not in auth:
                self.summary.skipped_unauthenticated+=1; logger.info('UPLOAD_SKIP build=%s channel=%s reason=unauthenticated',row['id'],channel or '(none)'); continue
            if available.get(channel,0)<=0:
                self.summary.skipped_cap+=1; logger.info('UPLOAD_SKIP build=%s channel=%s reason=daily_cap',row['id'],channel); continue
            plan['local_path']=path
            if self.upload_build(row['id'],plan): available[channel]-=1; uploaded+=1
        logger.info('UPLOAD_COMPLETE uploaded=%d skipped_cap=%d skipped_unauthenticated=%d missing=%d failed=%d',uploaded,self.summary.skipped_cap,self.summary.skipped_unauthenticated,self.summary.skipped_missing,self.summary.failed)
        return uploaded
    def _save_plan(self,plan):
        p=ensure_dir(config.data_dir/'plans')/(safe_slug(plan['slug'])+'.json'); p.write_text(json.dumps(plan,indent=2,default=str),encoding='utf-8'); logger.info('PLAN_WRITTEN path=%s',p); return p
    def report(self):
        logger.info('RUN_SUMMARY built=%d uploaded=%d errors=%d %s',self.stats['built'],self.stats['uploaded'],self.stats['errors'],self.summary.text()); print('\n=== RANKING PIPELINE SUMMARY ==='); print(f"Built: {self.stats['built']} | Uploaded: {self.stats['uploaded']} | Errors: {self.stats['errors']}"); print(self.summary.text()); return self.stats

def environment_check():
    try: which_ffmpeg(); print('[ok] ffmpeg')
    except Exception as e: print('[FAIL]',e); return 1
    try: print('[ok] font:',config.resolve_font())
    except Exception as e: print('[FAIL]',e); return 1
    print('[ok] topics:',', '.join(config.topic_names()) or '(none)'); print('[ok] authenticated ranking channels:',', '.join(sorted(authenticated_channels(Path(config.oauth_token_file)))) or '(none)'); print('[ok] runtime:',config.runtime_root); return 0

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument('--mode',choices=['once','auto','source','assemble','upload','test'],default='once'); p.add_argument('--topic'); p.add_argument('--plan'); p.add_argument('--no-upload',action='store_true'); p.add_argument('--dry-run',action='store_true'); a=p.parse_args(argv)
    if a.dry_run: config.dry_run=True
    if a.mode=='test': return environment_check()
    pipe=RankingPipeline()
    if a.mode=='upload': pipe.drain_queue(); pipe.report(); return 0
    if a.mode=='assemble':
        if not a.plan:return 2
        out=assembler.assemble(json.loads(Path(a.plan).read_text(encoding='utf-8'))); print(out or 'assembly failed'); return 0 if out else 1
    topic=a.topic or (pipe.db.next_topic(config.topic_names()) if a.mode=='auto' else (config.topic_names() or [None])[0])
    if not topic: return 2
    if a.mode=='source':
        clips=pipe.collect_clips(config.topic(topic),int(config.get('clips_per_video',5))); print(f'SOURCED topic={topic} clips={len(clips)}'); return 0
    result=pipe.build(topic,upload=not a.no_upload); pipe.report(); return 0 if result else 1
if __name__=='__main__': sys.exit(main())
