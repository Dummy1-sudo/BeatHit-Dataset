from __future__ import annotations
import argparse


def main() -> None:
    ap=argparse.ArgumentParser(prog='music-megalist')
    sp=ap.add_subparsers(dest='cmd',required=True)
    b=sp.add_parser('build'); b.add_argument('--all',action='store_true'); b.add_argument('dataset',nargs='?')
    sp.add_parser('build-all')
    fb=sp.add_parser('full-build'); fb.add_argument('--skip-zenodo',action='store_true'); fb.add_argument('--only',nargs='*')
    v=sp.add_parser('validate'); v.add_argument('--complete',action='store_true')
    sp.add_parser('dedupe')
    a=ap.parse_args()
    if a.cmd=='full-build':
        from .fullbuild import full_build
        full_build(skip_zenodo=a.skip_zenodo,only=a.only)
    elif a.cmd in {'build','build-all'}:
        from .builders import build_anime, build_megalist
        all_requested = a.cmd=='build-all' or getattr(a,'all',False)
        dataset = getattr(a,'dataset',None)
        if all_requested or dataset=='anime': build_anime()
        if all_requested: build_megalist()
    elif a.cmd=='dedupe':
        from .builders import build_megalist
        build_megalist()
    elif a.cmd=='validate':
        from .validate import validate
        errors=validate(require_complete=a.complete)
        if errors:
            print('\n'.join(errors)); raise SystemExit(1)
        print('OK')

if __name__=='__main__': main()
