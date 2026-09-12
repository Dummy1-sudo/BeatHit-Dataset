#!/usr/bin/env python3
"""Import both legacy (data-root) and current (repo-root) output artifacts."""
from pathlib import Path
import shutil
import sys
from stage_generated import GENERATED, ROOT


def restore(source: Path, root: Path = ROOT):
    repo_layout = (source / 'data').is_dir()
    for rel in GENERATED:
        if rel == 'STATUS.json' and not repo_layout:
            continue  # Legacy artifact did not contain STATUS; retain the repository copy.
        src = source / (rel if repo_layout else rel.removeprefix('data/'))
        dest = root / rel
        if src.is_dir():
            for item in src.rglob('*'):
                if item.is_file() and '.partial.' not in item.name:
                    target = dest / item.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
        elif src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


if __name__ == '__main__':
    restore(Path(sys.argv[1]))
