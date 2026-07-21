#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
EXCLUDE={'MANIFEST.json'}
rows=[]
for p in sorted(DATA.rglob('*')):
    if not p.is_file() or p.name in EXCLUDE or p.name.startswith('.') or '/raw/full_build/' in p.as_posix():
        continue
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    rows.append({'path':str(p.relative_to(ROOT)).replace('\\','/'),'bytes':p.stat().st_size,'sha256':h.hexdigest()})
(DATA/'MANIFEST.json').write_text(json.dumps({'schema_version':2,'files':rows},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
