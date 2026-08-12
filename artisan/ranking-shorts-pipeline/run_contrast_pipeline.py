#!/usr/bin/env python3
"""Build a ranking-style 'Others vs This Guy' contrast video.

Uses the existing ranking collector and renderer, but writes contrast copy:
ordinary clips become 'OTHERS ...'; the final clip becomes 'BUT THIS GUY'.
Set CONTRAST_SUBJECT=dog/person/pro/whatever to customize the punchline.
"""
from __future__ import annotations
import os,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent; sys.path.insert(0,str(HERE))
from src.main import RankingPipeline
from src.config import config
from src import scriptwriter

def main():
    topic=os.getenv('RANKING_CONTRAST_TOPIC') or (config.topic_names() or [None])[0]
    subject=os.getenv('CONTRAST_SUBJECT','GUY').upper()
    if not topic: print('No ranking topic configured'); return 2
    pipeline=RankingPipeline(); plan=pipeline.build(topic,upload=False)
    if not plan: return 1
    clips=plan.get('clips') or []
    for i,clip in enumerate(clips):
        action=(clip.get('title') or 'THIS').upper().replace('OTHERS ','').replace('BUT ','')
        clip['title'] = (f'BUT THIS {subject}' if i==len(clips)-1 else f'OTHERS {action}')[:72]
    plan['video_title']=f'OTHERS VS THIS {subject}'
    plan['upload_title']=plan['video_title']+' #Shorts'
    path=pipeline._save_plan(plan); print(f'contrast plan: {path}')
    return 0
if __name__=='__main__': raise SystemExit(main())
