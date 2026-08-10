"""Queue-first uploader: skip missing files and exhausted channels immediately."""
import argparse, random, time
from pathlib import Path
from .config import config
from .database import PipelineDatabase
from .uploader import YouTubeUploader
try:
    from .title_optimizer import optimize_title
except Exception:
    optimize_title = None


def run(niche=None, channel_override=None, limit=None):
    db=PipelineDatabase(); rows=db.unuploaded_shorts(limit=max(100, (limit or 999999)*3))
    if niche: rows=[r for r in rows if r.get('niche')==niche]
    if channel_override:
        rows=[r for r in rows if config.get_niche_channel(r.get('niche') or '')==channel_override]
    if not rows:
        print('UPLOAD SUMMARY\n  queued: 0\n  uploaded: 0\n  skipped: 0\n  message: no pending clips')
        return 0

    configured=[]
    for row in rows:
        ch=channel_override or config.get_niche_channel(row.get('niche') or '')
        if ch and ch not in configured: configured.append(ch)
    authed=set(config.authenticated_channels())
    default_token=Path(config.oauth_token_file).exists()
    cap=int(getattr(config,'upload_max_per_channel',6) or 6)
    budgets={ch:max(0,cap-db.uploaded_count_for_channel_since(ch)) for ch in configured}
    summary={'queued':len(rows),'uploaded':0,'missing':0,'unauthenticated':0,'cap_skips':0,'failed':0}
    grouped={ch:[] for ch in configured}
    for row in rows:
        ch=channel_override or config.get_niche_channel(row.get('niche') or '')
        grouped.setdefault(ch,[]).append(row)

    print('UPLOAD RUN'); print(f'  queued: {len(rows)}'); print(f'  channel cap: {cap} per 24h')
    for ch in configured:
        auth_ok=(not authed and default_token) or ch in authed
        print(f'  {ch}: budget {budgets[ch]}/{cap}, authenticated={"yes" if auth_ok else "no"}')

    for ch in configured:
        if budgets[ch]<=0:
            summary['cap_skips']+=len(grouped.get(ch,[])); print(f'  SKIP {ch}: daily cap reached, moving to next channel'); continue
        if authed and ch not in authed:
            summary['unauthenticated']+=len(grouped.get(ch,[])); print(f'  SKIP {ch}: no token, moving to next channel'); continue
        try: uploader=YouTubeUploader(channel=ch)
        except Exception as exc:
            summary['failed']+=len(grouped.get(ch,[])); print(f'  ERROR {ch}: authentication failed: {str(exc)[:160]}'); continue
        for row in grouped.get(ch,[]):
            if budgets[ch]<=0:
                summary['cap_skips']+=1; continue
            path=row.get('local_path') or ''
            if not path or not Path(path).exists():
                summary['missing']+=1; print(f"  SKIP {row['source_video_id']}#{row['segment_index']}: file missing"); continue
            hook=' '.join(str(row.get('title') or ch).split()).strip()
            title=optimize_title(hook,niche=row.get('niche') or ch,keywords=[],clip_index=row['segment_index']) if optimize_title else hook
            title=f'{title} #{row.get("niche") or ch} #Shorts'[:100]
            desc=f"Full video: https://youtube.com/watch?v={row['source_video_id']}\n\nFollow for more {row.get('niche') or ch} content!\n#Shorts #{row.get('niche') or ch}"
            print(f"  UPLOAD {row['source_video_id']}#{row['segment_index']} -> {ch}")
            try: vid=uploader.upload_short(path,title,desc,[row.get('niche') or ch,'Shorts'])
            except Exception as exc: vid=None; print(f'  ERROR {ch}: {str(exc)[:160]}')
            if not vid:
                summary['failed']+=1; continue
            db.mark_short_uploaded(row['source_video_id'],row['segment_index'],vid,channel=ch)
            summary['uploaded']+=1; budgets[ch]-=1
            if budgets[ch]>0 and config.upload_pacing_max:
                time.sleep(random.uniform(config.upload_pacing_min,config.upload_pacing_max))
        print(f'  DONE {ch}: uploaded={sum(1 for r in grouped.get(ch,[]) if r.get("youtube_short_id"))} remaining_budget={budgets[ch]}')

    print('\nUPLOAD SUMMARY')
    for key,val in summary.items(): print(f'  {key}: {val}')
    if all(v<=0 for v in budgets.values() if configured): print('  status: all authenticated channel budgets exhausted')
    else: print('  status: complete')
    return 0 if summary['failed']==0 else 1


def main():
    p=argparse.ArgumentParser(); p.add_argument('--niche'); p.add_argument('--channel'); p.add_argument('--limit',type=int); a=p.parse_args()
    return run(a.niche,a.channel,a.limit)
if __name__=='__main__': raise SystemExit(main())
