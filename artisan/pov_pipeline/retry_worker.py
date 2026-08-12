#!/usr/bin/env python3
"""Telegram callback worker for POV Retry buttons."""
from __future__ import annotations
import json, os, subprocess, time, urllib.parse, urllib.request
from pathlib import Path
HERE=Path(__file__).resolve().parent

def call(token, method, payload):
    data=urllib.parse.urlencode(payload).encode(); req=urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}',data=data,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=40) as r:return json.loads(r.read().decode())
    except Exception:return {}
def main():
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    if not token: print('TELEGRAM_BOT_TOKEN is not configured'); return 2
    offset=0; print('POV retry worker listening...')
    while True:
        result=call(token,'getUpdates',{'timeout':30,'offset':offset,'allowed_updates':json.dumps(['callback_query'])})
        for upd in result.get('result',[]):
            offset=upd['update_id']+1; cb=upd.get('callback_query') or {}; data=cb.get('data',''); cid=cb.get('id')
            call(token,'answerCallbackQuery',{'callback_query_id':cid,'text':'Retry queued'})
            if data.startswith('retry:'):
                project=data[6:]; subprocess.Popen([os.getenv('PYTHON','python'),str(HERE/'resume_project.py'),project],cwd=str(HERE))
            elif data=='resume:latest':
                subprocess.Popen([os.getenv('PYTHON','python'),str(HERE/'resume_project.py')],cwd=str(HERE))
        time.sleep(1)
if __name__=='__main__': raise SystemExit(main())
