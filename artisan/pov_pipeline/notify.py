#!/usr/bin/env python3
"""POV notifications with actionable Telegram retry buttons."""
from __future__ import annotations
import json, os, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from typing import Callable
import povconfig
from povconfig import log_line, resolve_secret
Notify = Callable[[str, str], None]
API = 'https://api.telegram.org'; DUPLICATE_WINDOW_S = 60
ALERT_EVENTS = {'gate.fail','gate.needs_review','agent.failed','chain.abort','images.failed','upload.failed','daemon.fatal'}
PREFIX = {'gate.fail':'WARN','gate.needs_review':'NEEDS REVIEW','agent.failed':'FAILED','chain.abort':'ABORTED','images.failed':'FAILED','upload.failed':'FAILED','daemon.fatal':'FATAL'}
def default_config_path():
    live = povconfig.secrets_dir() / 'notify.env'
    return live if live.exists() else povconfig.config_dir() / 'notify.env.template'
def load_credentials(path=None):
    values={}; p=Path(path or default_config_path())
    if p.exists():
        for line in p.read_text(encoding='utf-8-sig',errors='replace').splitlines():
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1); values[k.strip()]=v.strip().strip('"').strip("'")
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip() or resolve_secret(values.get('TELEGRAM_BOT_TOKEN',''))
    chat=os.getenv('TELEGRAM_CHAT_ID','').strip() or resolve_secret(values.get('TELEGRAM_CHAT_ID',''))
    return token or None, chat or None
def send_telegram(token, chat_id, text, retry_project=''):
    payload={'chat_id':chat_id,'text':text[:4000],'disable_web_page_preview':'false'}
    if retry_project:
        payload['reply_markup']=json.dumps({'inline_keyboard':[[{'text':'Retry project','callback_data':f'retry:{retry_project}'}],[{'text':'Resume latest','callback_data':'resume:latest'}]]})
    data=urllib.parse.urlencode(payload).encode(); req=urllib.request.Request(f'{API}/bot{token}/sendMessage',data=data,method='POST'); req.add_header('Content-Type','application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req,timeout=10) as resp: return bool(json.loads(resp.read().decode()).get('ok'))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError): return False
def format_message(event,message):
    prefix=PREFIX.get(event); return (f'[{prefix}] ' if prefix else '') + message + f'\n\n({event})'
def make_notifier(config_path=None, *, log_path=None):
    token,chat=load_credentials(config_path); configured=bool(token and chat); recent={}
    def notify(event,message):
        log_line(f'notify:{event}',message,echo=False,path=log_path)
        if not configured or event not in ALERT_EVENTS: return
        key=f'{event}|{message}'; now=time.time()
        if now-recent.get(key,0)<DUPLICATE_WINDOW_S:return
        recent[key]=now
        project=''
        parts=message.split(':',1)
        if len(parts)>1: project=parts[0].replace('POV ','').strip().split()[0]
        send_telegram(token,chat,format_message(event,message),project)
    return notify
def null_notifier(): return lambda _event,_message: None
if __name__=='__main__':
    t,c=load_credentials(); print('token:', 'set' if t else 'NOT SET'); print('chat id:', c or 'NOT SET')
