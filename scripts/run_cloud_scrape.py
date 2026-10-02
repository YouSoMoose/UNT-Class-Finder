"""Bounded unattended run with periodic private checkpoints and graceful stopping."""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', choices=['spring-2027', 'fall-2026'], default='fall-2026')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--hours', type=float, default=4)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if not 0 < args.hours <= 4.5:
        parser.error('--hours must be between 0 and 4.5')
    path = Path(f'private-data/{args.dataset}.json')
    command = [sys.executable, 'scraper/class_scraper.py', '--output', str(path), '--workers', '3', '--delay', '2', '--retries', '2']
    command += ['--term', '2026 Fall' if args.dataset == 'fall-2026' else '2027 Spring', '--input', 'catalog_courses.json']
    if args.refresh:
        command.append('--refresh')
    if args.limit:
        command += ['--limit', str(args.limit)]
    upload = [sys.executable, 'scripts/cloud_data.py', 'checkpoint', '--file', str(path), '--dataset', args.dataset]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    deadline = time.monotonic() + args.hours * 3600
    next_upload = time.monotonic() + 60
    failed_upload = False
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.terminate()
                break
            if time.monotonic() >= next_upload:
                if path.exists():
                    subprocess.run(upload, check=True, timeout=90)
                next_upload = time.monotonic() + 60
            time.sleep(1)
    except BaseException:
        failed_upload = True
        process.terminate()
        raise
    finally:
        try:
            process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if path.exists():
            subprocess.run(upload, check=True, timeout=90)
    return process.returncode or int(failed_upload)

if __name__ == '__main__':
    raise SystemExit(main())
