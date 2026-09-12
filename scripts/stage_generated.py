#!/usr/bin/env python3
"""Stage generated output paths, including deletions, without staging source inputs."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GENERATED = (
    'STATUS.json', 'data/BUILD_REPORT.json', 'data/coverage_report.csv',
    'data/MANIFEST.json', 'data/target_report.json', 'data/complete_validation.txt',
    'data/hololive_coverage.json',
    *('data/' + name for name in (
        'anime', 'vocaloid', 'worldwide', 'classical', 'emerging', 'genres',
        'screen_soundtracks', 'vtuber_original', 'vtuber_non_original', 'video_games',
        'kpop', 'internet_native', 'electronic_subcultures', 'alternative_extreme',
        'jazz_depth', 'children_childhood', 'unserious', 'special_required', 'countries', 'megalist')),
)


def main():
    tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
    paths = [p for p in GENERATED if (ROOT / p).exists() or any(f == p or f.startswith(p + '/') for f in tracked)]
    if paths:
        subprocess.run(['git', 'add', '-A', '--', *paths, ':(exclude)**/*.partial.*'], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
