#!/usr/bin/env python3
"""Fresh sweep only for authenticated, configured upload channels."""
from __future__ import annotations
import subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent; sys.path.insert(0,str(HERE))
from src.config import config

def main():
    authed=set(config.authenticated_channels())
    if not authed:
        print('[sweep] no authenticated channels found',file=sys.stderr); return 2
    seen=set(); rc=0
    for niche in config.niche_names():
        targets=set(config.get_niche_channels(niche))
        active=sorted(targets & authed)
        if not active:
            continue
        if niche in seen: continue
        seen.add(niche)
        print(f'[sweep] {niche}: {", ".join(active)}')
        result=subprocess.run([sys.executable,'-m','src.main','--mode','once','--niche',niche,'--videos','1'],cwd=str(HERE))
        rc=rc or result.returncode
    return rc
if __name__=='__main__': raise SystemExit(main())
