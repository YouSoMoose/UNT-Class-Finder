#!/usr/bin/env python3
"""Fetch all catalog pages, resuming at page checkpoints without guessing the end."""
import argparse
from contextlib import ExitStack
import csv
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urljoin, urlencode
import requests
from bs4 import BeautifulSoup
from scraper_common import atomic_json, load_json, RunControl, RunLock, now

SOURCES = {'undergraduate': (40, 4629), 'graduate': (39, 4531)}
COURSE_CODE_RE = re.compile(r'^([A-Z]{2,8})\s+(\d{3,4}[A-Z]?)\s*[-–—]\s*(.+)$')


def page_url(catalog, page):
    catoid, navoid = SOURCES[catalog]
    return 'https://catalog.unt.edu/content.php?' + urlencode({
        'catoid': catoid, 'navoid': navoid, 'filter[item_type]': 3,
        'filter[only_active]': 1, 'filter[3]': 1, 'filter[cpage]': page})


def parse_page(html, catalog, url):
    soup = BeautifulSoup(html, 'html.parser')
    records = {}
    for a in soup.select('a[href*="preview_course"]'):
        text = ' '.join(a.get_text(' ', strip=True).split())
        match = COURSE_CODE_RE.match(text)
        if not match:
            continue
        subject, number, title = match.groups()
        code = f'{subject} {number}'
        records[code] = {'catalog': catalog, 'course_code': code, 'subject': subject,
                         'course_number': number, 'course_name': title,
                         'source_url': url, 'course_url': urljoin(url, a.get('href', ''))}
    if not records:
        raise RuntimeError('No course anchors found. This is an access/error page or the page format changed; it is not treated as the end of the catalog.')
    pages = {int(parse_qs(urlparse(url).query).get('filter[cpage]', ['1'])[0])}
    catoid, navoid = SOURCES[catalog]
    for a in soup.select('a[href]'):
        q = parse_qs(urlparse(urljoin(url, a['href'])).query)
        if str(catoid) not in q.get('catoid', []) or str(navoid) not in q.get('navoid', []):
            continue
        for value in q.get('filter[cpage]', []):
            if value.isdigit() and 1 <= int(value) <= 500:
                pages.add(int(value))
    return list(records.values()), max(pages)


def fetch(session, url, retries, control):
    for attempt in range(retries + 1):
        if not control.wait():
            raise InterruptedError('Stopped')
        try:
            r = session.get(url, timeout=(10, 30))
            # Never retry forbidden/challenge pages as though they were connectivity errors.
            if r.status_code in (202, 401, 403, 429):
                raise RuntimeError(f'Catalog returned HTTP {r.status_code}. Try --browser --headed so the page can run JavaScript; checkpoint retained.')
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            if attempt == retries:
                raise RuntimeError(f'Network unavailable after {retries + 1} attempts; restart this command when Wi-Fi returns.') from exc
            seconds = min(2 ** (attempt + 1), 15)
            print(f'Network error; retrying in {seconds}s…', file=sys.stderr)
            if control.stop.wait(seconds):
                raise InterruptedError('Stopped')


class BrowserCatalog:
    """One persistent browser session for the entire crawl, as in the original scraper."""
    def __init__(self, stack, headed=False, profile=None, executable=None):
        from playwright.sync_api import sync_playwright
        pw = stack.enter_context(sync_playwright())
        launch = {'headless': not headed}
        if executable:
            launch['executable_path'] = executable
        if profile:
            context = pw.chromium.launch_persistent_context(str(profile), **launch)
        else:
            browser = pw.chromium.launch(**launch)
            stack.callback(browser.close)
            context = browser.new_context()
        stack.callback(context.close)
        self.page = context.new_page()

    def fetch(self, url, retries, control):
        from playwright.sync_api import TimeoutError as BrowserTimeout
        for attempt in range(retries + 1):
            if not control.wait():
                raise InterruptedError('Stopped')
            try:
                response = self.page.goto(url, wait_until='domcontentloaded', timeout=45000)
                # HTTP 202 can be an intermediate JavaScript response. Let the normal browser
                # execute the page, then accept only real catalog course anchors.
                if response and response.status in (401, 403, 429):
                    raise RuntimeError(f'Catalog browser returned HTTP {response.status}; checkpoint retained.')
                try:
                    self.page.locator('a[href*="preview_course"]').first.wait_for(state='attached', timeout=30000)
                except BrowserTimeout as exc:
                    raise RuntimeError('Catalog did not render course links. It may require a browser check or the page format changed. Progress is retained; inspect the visible browser. No access-check bypass is attempted.') from exc
                return self.page.content()
            except RuntimeError:
                raise
            except Exception as exc:
                if attempt == retries:
                    raise RuntimeError(f'Browser connection failed after {retries + 1} attempts. Resume when Wi-Fi returns.') from exc
                delay = min(2 ** (attempt + 1), 15)
                print(f'Browser network error; retry in {delay}s…', file=sys.stderr)
                if control.stop.wait(delay):
                    raise InterruptedError('Stopped')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path('catalog_courses.json'))
    p.add_argument('--checkpoint', type=Path, default=Path('checkpoints/catalog.json'))
    p.add_argument('--delay', type=float, default=0.25)
    p.add_argument('--retries', type=int, default=3)
    p.add_argument('--refresh', action='store_true', help='Start a fresh catalog crawl; previous published output stays until complete')
    p.add_argument('--html', type=Path, nargs='+', help='Seed the checkpoint with saved catalog pages (catalog/page detected from links)')
    p.add_argument('--offline', action='store_true', help='Only import --html pages, without network requests')
    p.add_argument('--csv-output', type=Path)
    p.add_argument('--browser', action='store_true', help='Use Chromium with JavaScript, as in the original scraper')
    p.add_argument('--headed', action='store_true', help='Show the browser (implies --browser)')
    p.add_argument('--profile', type=Path, help='Keep local browser session across runs; do not share this folder')
    p.add_argument('--browser-path', help='Optional installed Chrome/Chromium executable')
    args = p.parse_args()
    if args.delay < 0 or args.retries < 0:
        p.error('Delay and retries must be nonnegative')
    state = {} if args.refresh else load_json(args.checkpoint)
    if state and state.get('schema_version') != 2:
        raise SystemExit('Unrecognized catalog checkpoint schema')
    state = state or {'schema_version': 2, 'sources': {}}
    control = RunControl()
    def store(catalog, number, records, last):
        source = state['sources'].setdefault(catalog, {'pages': {}, 'last_page': 1})
        source['pages'][str(number)] = records
        source['last_page'] = max(last, source['last_page'])
        state['updated_at'] = now()
        atomic_json(args.checkpoint, state)
    try:
        with RunLock(args.checkpoint), requests.Session() as session, ExitStack() as stack:
            session.headers['User-Agent'] = 'UNT-Schedule-Builder/2.0 (course catalog index)'
            for file in args.html or []:
                html = file.read_text(encoding='utf-8')
                soup = BeautifulSoup(html, 'html.parser')
                candidates = [a.get('href', '') for a in soup.select('a[href*="navoid"]')]
                inferred = None
                for url in candidates:
                    q = parse_qs(urlparse(url).query)
                    for catalog, (catoid, navoid) in SOURCES.items():
                        if str(catoid) in q.get('catoid', []) and str(navoid) in q.get('navoid', []):
                            # Skip/top/print links encode the saved current page. Numeric pagination links do not.
                            a = soup.find('a', href=url)
                            text = a.get_text(' ', strip=True)
                            if text in ('Skip to Content', 'Top', 'Print (opens a new window)'):
                                inferred = (catalog, int(q.get('filter[cpage]', ['1'])[0]))
                                break
                    if inferred:
                        break
                if not inferred:
                    # First-page saved files may lack current-page links; current unlinked pagination text identifies it.
                    for catalog, (catoid, navoid) in SOURCES.items():
                        if any(str(catoid) in parse_qs(urlparse(u).query).get('catoid', []) for u in candidates):
                            inferred = (catalog, 1)
                            break
                if not inferred:
                    raise RuntimeError(f'Could not identify the catalog in {file}')
                catalog, number = inferred
                records, last = parse_page(html, catalog, page_url(catalog, number))
                store(catalog, number, records, last)
                print(f'Imported {catalog} page {number}: {len(records)} courses; {last} pages in catalog.')
            if not args.offline:
                browser = BrowserCatalog(stack, args.headed, args.profile, args.browser_path) if (args.browser or args.headed or args.profile) else None
                print('Catalog mode: ' + ('browser (JavaScript enabled)' if browser else 'HTTP requests'))
                control.start_console()
                for catalog in SOURCES:
                    source = state['sources'].setdefault(catalog, {'pages': {}, 'last_page': 1})
                    number = 1
                    while number <= source['last_page']:
                        if not control.wait():
                            raise InterruptedError('Stopped')
                        if str(number) not in source['pages']:
                            url = page_url(catalog, number)
                            html = browser.fetch(url, args.retries, control) if browser else fetch(session, url, args.retries, control)
                            records, last = parse_page(html, catalog, url)
                            store(catalog, number, records, last)
                            print(f'{catalog}: {len(source["pages"])}/{source["last_page"]} pages saved • {sum(len(x) for x in source["pages"].values())} course records')
                            if control.stop.wait(args.delay):
                                raise InterruptedError('Stopped')
                        number += 1
            complete = all(c in state['sources'] and len(state['sources'][c]['pages']) == state['sources'][c]['last_page'] for c in SOURCES)
            merged = {}
            for catalog, source in state['sources'].items():
                for records in source['pages'].values():
                    for record in records:
                        merged[(catalog, record['course_code'])] = record
            if complete:
                records = sorted(merged.values(), key=lambda r: (r['catalog'], r['course_code']))
                atomic_json(args.output, records)
                atomic_json('public/data/catalog-index.json', {r['course_code']: r['course_name'] for r in records})
                if args.csv_output:
                    args.csv_output.parent.mkdir(parents=True, exist_ok=True)
                    with args.csv_output.open('w', newline='', encoding='utf-8') as f:
                        w = csv.DictWriter(f, fieldnames=list(records[0]))
                        w.writeheader(); w.writerows(records)
                print(f'COMPLETE: {len(records)} catalog records published to {args.output}')
            else:
                print(f'{len(merged)} records checkpointed. Crawl incomplete; existing {args.output} is unchanged. Run again to resume.')
    except (KeyboardInterrupt, InterruptedError):
        print('\nStopped safely. Run the same command to resume from the last saved page.')
    except Exception as exc:
        print(f'\nStopped: {exc}\nCompleted pages remain in {args.checkpoint}.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
