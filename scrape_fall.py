"""Use the VSB scraper engine with isolated Fall paths and fixed term."""
import sys
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scraper'))
import class_scraper

if __name__ == '__main__':
    # Callers may choose workers/mode, but cannot redirect Fall writes into Spring.
    if any(arg.split('=')[0] in ('--term', '--input', '--output', '--profile') for arg in sys.argv[1:]):
        raise SystemExit('Fall input, output and term are fixed. Do not share Spring browser profiles.')
    sys.argv += ['--term', '2026 Fall', '--input', str(ROOT / 'catalog_courses.json'), '--output', str(ROOT / 'public/data/fall-2026.json')]
    os.chdir(ROOT)
    raise SystemExit(class_scraper.main())
