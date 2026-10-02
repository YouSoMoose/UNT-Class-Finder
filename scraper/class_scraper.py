#!/usr/bin/env python3
"""Resumable Spring 2027 myUNT scraper using the public homepage search flow."""
import argparse
import json
import re
import sys
import time
import threading
import signal
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue, Empty
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from scraper_common import atomic_json, load_json, RunLock, RunControl, now

HOME_URL = 'https://my.unt.edu/psc/ps/EMPLOYEE/SA/c/NUI_FRAMEWORK.PT_LANDINGPAGE.GBL'
SEARCH_URL = HOME_URL  # Compatibility for code importing the old constant.
DAY_CODES = {'Monday':'M', 'Tuesday':'T', 'Wednesday':'W', 'Thursday':'R', 'Friday':'F', 'Saturday':'S', 'Sunday':'U'}


def is_sign_in_gate(text):
    text = ' '.join((text or '').lower().split())
    return 'sign in to peoplesoft' in text or ('sign in' in text and 'peoplesoft' in text)


def parse_days_and_times(value):
    normalized = ' '.join((value or '').split())
    days = ''.join(code for name, code in DAY_CODES.items() if name.lower() in normalized.lower())
    match = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)\s*(?:to|[-–])\s*(\d{1,2}:\d{2}\s*[AP]M)', normalized, re.I)
    return (days, match[1].replace(' ', '').upper(), match[2].replace(' ', '').upper()) if match else (days, '', '')


def clean(value):
    return ' '.join(value.split())


def parse_class_table(html, *, allow_partial=False):
    """One option is one indivisible enrollment bundle; keep every paired class."""
    soup = BeautifulSoup(html, 'html.parser')
    table = next((t for t in soup.select('table') if 'Days and Times' in t.get_text(' ', strip=True)
                  and 'Section' in t.get_text(' ', strip=True)), None)
    if not table:
        raise RuntimeError('Expected Class Selection table is missing; result is not marked no_classes.')
    options = []
    for row in table.select('tr'):
        cells = row.find_all('td', recursive=False)
        if len(cells) == 8 and re.search(r'\b\d+\s+of\s+\d+\b', cells[7].get_text(' ', strip=True)):
            cells.append(soup.new_tag('td'))
        if len(cells) < 9:
            continue
        texts = [clean(c.get_text(' ', strip=True)) for c in cells]
        components = re.findall(r'Section\s+(\w+)\s*[-–]\s*Class Nbr\s+(\d+)', texts[3], re.I)
        if not components:
            continue
        # Each linked component has a numbered schedule container, e.g. MTG_SCHED_L_1 / _2.
        containers = [c for c in cells[5].select('[id*="SSR_MTG_SCHED_L_"]')
                      if not c.select('[id*="SSR_MTG_SCHED_L_"]')]
        times = [clean(c.get_text(' ', strip=True)) for c in containers]
        if not times:
            times = [texts[5]]
        # Older PeopleSoft markup can flatten several patterns into one container.
        if len(times) == 1:
            split_times = re.findall(r'(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*)+\d{1,2}:\d{2}\s*[AP]M\s*(?:to|[-–])\s*\d{1,2}:\d{2}\s*[AP]M', times[0], re.I)
            if len(split_times) > 1:
                times = split_times
        def pieces(cell, count):
            # Multiple component values are separate spans in PeopleSoft.
            spans = cell.find_all('span')
            leaf = [clean(s.get_text(' ', strip=True)) for s in spans if not s.find('span')]
            leaf = [s for s in leaf if s]
            if len(leaf) == count:
                return leaf
            lines = [clean(s) for s in cell.get_text('\n', strip=True).splitlines() if clean(s)]
            return lines if len(lines) == count else [clean(cell.get_text(' ', strip=True))] * count
        count = len(components)
        if count > 1 and len(times) != count:
            raise RuntimeError(f'Option {texts[0]} has {count} linked sections but {len(times)} schedule blocks. Refusing a partial parse.')
        # Arranged rows sometimes omit Room and shift Instructor/Seats left.
        missing_room = bool(re.search(r'\b\d+\s+of\s+\d+\b', texts[7])) and not re.search(r'\b\d+\s+of\s+\d+\b', texts[8])
        pattern_count = len(times) if count == 1 else count
        instructors = pieces(cells[6 if missing_room else 7], pattern_count)
        seat_values = pieces(cells[7 if missing_room else 8], count)
        rooms = ['No Facility Assigned'] * pattern_count if missing_room else pieces(cells[6], pattern_count)
        for i, room in enumerate(rooms):
            found = re.findall(r'\b[A-Za-z][A-Za-z0-9-]{1,15}\s+[A-Za-z]?\d[\w-]*\b', room)
            if len(found) > 1:
                if len({r.lower() for r in found}) == 1:
                    rooms[i] = found[0]
                elif len(found) == pattern_count:
                    rooms = found
                    break
                else:
                    raise RuntimeError('Ambiguous room-to-meeting mapping; refusing a partial parse.')
            elif ',' in room and not re.search(r'\b\d', room):
                raise RuntimeError('Instructor text appeared in Room; refusing shifted columns.')
        dates = pieces(cells[4], count)
        meetings = []
        linked = []
        for i, (section, nbr) in enumerate(components):
            time_text = times[min(i, len(times)-1)]
            days, start, end = parse_days_and_times(time_text)
            seats = re.search(r'(\d+)\s+of\s+(\d+)', seat_values[i])
            meeting = {'days_and_times': time_text, 'days': days, 'start_time': start, 'end_time': end,
                       'room': rooms[i], 'instructor': instructors[i], 'meeting_dates': dates[i],
                       'section': section, 'class_number': nbr}
            linked.append({**meeting, 'open_seats': int(seats[1]) if seats else None,
                           'total_seats': int(seats[2]) if seats else None})
            meetings.append(meeting)
        first = linked[0]
        # A single class may itself have several meeting patterns. Preserve additional blocks.
        if count == 1 and len(times) > 1:
            meetings = []
            pattern_dates = pieces(cells[4], pattern_count)
            date_values = re.findall(r'\d{1,2}/\d{1,2}/\d{4}\s*[-–]\s*\d{1,2}/\d{1,2}/\d{4}', texts[4])
            if len(date_values) == pattern_count:
                pattern_dates = date_values
            elif len(set(date_values)) > 1:
                raise RuntimeError('Ambiguous date-to-meeting mapping; refusing a partial parse.')
            for index, text in enumerate(times):
                days, start, end = parse_days_and_times(text)
                meetings.append({**first, 'days_and_times': text, 'days': days, 'start_time': start, 'end_time': end, 'room': rooms[index], 'instructor': instructors[index], 'meeting_dates': pattern_dates[index]})
        options.append({**first, 'section': ' + '.join(s for s, _ in components),
                        'class_number': '+'.join(n for _, n in components),
                        'class_numbers': [n for _, n in components], 'linked_sections': linked,
                        'option': texts[0], 'component': 'Enrollment option', 'status': texts[1],
                        'session': texts[2], 'days_and_times': ' • '.join(times),
                        'meetings': meetings})
    if not options:
        raise RuntimeError('Class Selection table contains no recognized options; possible UI change.')
    # Do not silently accept a paginated/truncated options table.
    option_counts = re.findall(r'\b(\d+)\s+options\b', soup.get_text(' ', strip=True), re.I)
    if not allow_partial and option_counts and max(map(int, option_counts)) > len(options):
        raise RuntimeError(f'Only {len(options)} of {max(map(int, option_counts))} options loaded. Expand the table before retrying.')
    return options


def course_payload(course_code, sections, status, message):
    return {'course_code': course_code, 'status': status, 'message': message,
            'sections': sections, 'section_count': len(sections), 'fetched_at': now()}

class UNTSearch:
    def __init__(self, page, term):
        self.page = page
        self.term = term
        self.ready = False
        self.search_url = None
        page.set_default_timeout(20000)
        page.set_default_navigation_timeout(45000)

    def targets(self):
        return [self.page] + [f for f in self.page.frames if f != self.page.main_frame]

    def text(self):
        return '\n'.join(t.locator('body').inner_text(timeout=5000) for t in self.targets())

    def wait_control(self, selectors=None, name=None, role=None, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for target in self.targets():
                locators = [target.locator(s) for s in selectors or []]
                if name is not None:
                    locators.append(target.get_by_role(role, name=name, exact=True) if role else target.get_by_text(name, exact=True))
                for locator in locators:
                    try:
                        for loc in locator.all():
                            if loc.is_visible():
                                return loc
                    except Exception:
                        continue
            self.page.wait_for_timeout(100)
        text = self.text()
        if is_sign_in_gate(text):
            raise RuntimeError('Sign-in gate: use --headed --profile .unt-profile and sign in locally if required.')
        raise TimeoutError(f'Expected control not visible: {name or selectors}')

    def settle(self):
        # Wait for observed PeopleSoft processing indicator to disappear. No arbitrary multi-second sleep.
        for target in self.targets():
            try:
                target.locator('[id="processing"]').wait_for(state='hidden', timeout=30000)
            except Exception:
                pass

    def initialize(self):
        self.page.goto(HOME_URL, wait_until='domcontentloaded')
        self.wait_control(name='Search for Classes').click()
        self.wait_control(name=self.term, role='link').click()
        self.wait_control(selectors=['input[id="PTS_KEYWORDS3"]'])
        self.settle()
        self.search_url = self.page.url
        self.ready = True

    def collect_options(self, target):
        """Expand myUNT's cumulative table until every advertised option is loaded."""
        html = target.content()
        while True:
            options = parse_class_table(html, allow_partial=True)
            counts = re.findall(r'\b(\d+)\s+options\b',
                                BeautifulSoup(html, 'html.parser').get_text(' ', strip=True), re.I)
            expected = max(map(int, counts)) if counts else len(options)
            if len(options) >= expected:
                return parse_class_table(html)
            # Observed myUNT control: Display 10 More (the number can vary).
            more = target.get_by_role('button', name=re.compile(r'^Display\s+\d+\s+More$', re.I))
            if not more.count() or not more.first.is_visible() or not more.first.is_enabled():
                raise RuntimeError(f'Only {len(options)} of {expected} options loaded; Display More is unavailable.')
            previous = len(options)
            print(f'  Loading remaining enrollment options: {previous}/{expected}', flush=True)
            more.first.click()
            # Processing can appear after click returns, so wait for actual row growth.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                self.page.wait_for_timeout(200)
                html = target.content()
                try:
                    expanded = parse_class_table(html, allow_partial=True)
                except RuntimeError:
                    continue  # PeopleSoft may briefly replace the table during its update.
                if len(expanded) > previous:
                    break
            else:
                raise TimeoutError(f'Display More did not load additional options ({previous}/{expected}). Progress is retained.')

    def reset(self):
        if not self.ready:
            self.initialize()
            return
        for target in self.targets():
            field = target.locator('input[id="PTS_KEYWORDS3"]')
            if field.count() and field.first.is_visible():
                return
        if 'Course Information' in self.text():
            self.wait_control(selectors=['[id="PT_WORK_PT_BUTTON_BACK"]']).click()
            self.wait_control(selectors=['[id="SSR_CLSRCH_ES_FL"]'])
            self.settle()
        self.wait_control(selectors=['[id="PT_WORK_PT_BUTTON_BACK"]']).click()
        self.wait_control(selectors=['input[id="PTS_KEYWORDS3"]'])
        self.settle()

    def scrape(self, code):
        self.reset()
        field = self.wait_control(selectors=['input[id="PTS_KEYWORDS3"]'])
        field.fill(code)
        self.wait_control(selectors=['[id="PTS_SRCH_BTN"]']).click()
        self.settle()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            text = self.text()
            # Inspect only after the search results page arrives; stale search page is never no_classes.
            if any(t.locator('[id="SSR_CLSRCH_ES_FL"]').count() for t in self.targets()):
                break
            if re.search(r'no (?:results|classes|courses) (?:were )?found|search returned no results', text, re.I):
                return [], 'no_classes', f'No scheduled results for {code} in {self.term}'
            self.page.wait_for_timeout(100)
        else:
            raise TimeoutError('Search results did not load')
        # Observed search default: Open Classes Only. Remove it to preserve closed/waitlist records.
        for target in self.targets():
            remove = target.get_by_role('button', name='Remove Open Classes Only filter', exact=True)
            if remove.count() and remove.first.is_visible():
                remove.first.click()
                self.settle()
                remove.first.wait_for(state='hidden', timeout=20000)
                break
        text = self.text()
        result = None
        for target in self.targets():
            loc = target.get_by_role('link', name=code, exact=True)
            if loc.count() and loc.first.is_visible():
                result = loc.first
                break
        if result is None:
            if 'exceeded a limit' in text.lower():
                raise RuntimeError('Results are truncated; cannot determine whether this course is absent.')
            if re.search(r'no (?:results|classes|courses)|\b0\s+courses', text, re.I) or 'courses displayed with keyword' in text:
                return [], 'no_classes', f'No exact course result for {code} in {self.term}'
            raise RuntimeError('Search results are ambiguous; course was not marked complete.')
        result.click()
        self.wait_control(name='Class Selection')
        self.settle()
        # Term must appear in the course header, not just a requested URL.
        if self.term not in self.text():
            raise RuntimeError('Course page term does not match requested term')
        for target in self.targets():
            tables = target.locator('table').filter(has_text='Days and Times').filter(has_text='Section')
            if tables.count():
                # Read the DOM once, parse locally; substantially fewer browser round trips.
                options = self.collect_options(target)
                return options, 'ok', f'{len(options)} enrollment options in {self.term}'
        raise RuntimeError('Class Selection table did not load')


def scrape_course(page, course_code, term='2027 Spring'):
    try:
        return UNTSearch(page, term).scrape(course_code)
    except Exception as exc:
        return [], 'scraper_failure', str(exc)

load_progress = load_json
save_progress = atomic_json
classify_result = course_payload


def load_course_codes(path):
    return list(dict.fromkeys(r['course_code'] for r in load_json(path, []) if r.get('course_code')))


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument('--input', type=Path, default=Path('catalog_courses.json'))
    p.add_argument('--output', type=Path, default=Path('public/data/fall-2026.json'))
    p.add_argument('--term', default='2026 Fall')
    p.add_argument('--courses', nargs='+', help='Quoted course codes, e.g. "MATH 1680" "PHYS 2220"')
    p.add_argument('--limit', type=int)
    p.add_argument('--workers', type=int, default=3, choices=(1, 2, 3, 4), help='Independent browser sessions; default 3, maximum 4')
    p.add_argument('--interactive', action='store_true', help='Ask for resume/refresh mode and worker count before starting')
    p.add_argument('--delay', type=float, default=0.25)
    p.add_argument('--retries', type=int, default=2)
    p.add_argument('--headed', action='store_true')
    p.add_argument('--profile', type=Path, help='Persistent local browser profile (one worker only)')
    p.add_argument('--browser-path', help='Optional installed Chromium/Chrome executable')
    p.add_argument('--refresh', action='store_true')
    p.add_argument('--status', action='store_true', help='Show saved coverage without opening a browser')
    a = p.parse_args()
    if not re.fullmatch(r'\d{4} (Spring|Summer|Fall)', a.term):
        p.error('Term must be like "2027 Spring"')
    if a.profile and a.workers != 1 and not a.interactive:
        p.error('Persistent profiles require --workers 1')
    if a.delay < 0 or a.retries < 0 or (a.limit is not None and a.limit < 1):
        p.error('Delay/retries must be nonnegative; limit positive')
    return a


def startup_choices(a, pending_count, total_count, ask=input):
    """Collect choices before starting the pause/resume command listener."""
    print(f'\n{a.term}: {pending_count} missing/failed; {total_count} total courses.')
    print('1 = Resume: scrape only missing/failed courses (default).')
    print('2 = Start from beginning: refresh every course; keep saved data until replaced successfully.')
    while True:
        choice = ask('Choose 1 or 2 [1]: ').strip() or '1'
        if choice in ('1', '2'):
            a.refresh = choice == '2'
            break
        print('Enter 1 or 2.')
    if a.profile:
        a.workers = 1
        print('Using 1 worker for your persistent browser profile.')
        return
    print('Headless browsers run without visible windows. Start with 3; try 4 if your PC and myUNT handle it well.')
    while True:
        choice = ask(f'Browser workers (1-4) [{a.workers}]: ').strip() or str(a.workers)
        if choice in ('1', '2', '3', '4'):
            a.workers = int(choice)
            return
        print('Enter a number from 1 to 4.')


def main():
    a = parse_args()
    catalog_codes = load_course_codes(a.input)
    all_codes = a.courses or catalog_codes
    data = load_json(a.output)
    if data and data.get('term') != a.term:
        raise SystemExit('Output belongs to another term or legacy format. Choose a different --output.')
    data = data or {'schema_version': 2, 'term': a.term, 'courses': {}, 'updated_at': None}
    records = data['courses']
    refresh_pending = set(data.get('refresh_pending', []))
    if a.refresh and not refresh_pending:
        refresh_pending = set(all_codes)
    pending = [c for c in all_codes if c in refresh_pending or records.get(c, {}).get('status') not in ('ok', 'no_classes') or records.get(c, {}).get('last_error')]
    if a.status:
        print(f'{a.term}: {len(all_codes) - len(pending)}/{len(all_codes)} completed; {len(pending)} pending/retryable')
        print(dict(Counter(r.get('status') for r in records.values())))
        return 0
    if a.interactive:
        retry_count = sum(records.get(c, {}).get('status') not in ('ok', 'no_classes') or bool(records.get(c, {}).get('last_error')) for c in all_codes)
        try:
            startup_choices(a, retry_count, len(all_codes))
        except (EOFError, KeyboardInterrupt):
            print('\nCancelled before starting; saved progress is unchanged.')
            return 0
        if a.refresh and not refresh_pending:
            refresh_pending = set(all_codes)
        pending = [c for c in all_codes if c in refresh_pending or records.get(c, {}).get('status') not in ('ok', 'no_classes') or records.get(c, {}).get('last_error')]
    if a.limit:
        pending = pending[:a.limit]
    if not pending:
        print('Nothing pending. Use --refresh to update existing data.')
        return 0
    print(f'{a.term}: {len(records)} saved records; {len(pending)} jobs this run; {a.workers} worker(s).')
    control = RunControl()
    queue = Queue()
    for code in pending:
        queue.put(code)
    write_lock = threading.Lock()
    count = 0
    started = time.monotonic()
    failures = 0
    total_failures = 0
    signal.signal(signal.SIGTERM, lambda *_: control.stop.set())
    def checkpoint():
        data['refresh_pending'] = sorted(refresh_pending)
        data['updated_at'] = now()
        data['coverage'] = {'catalog_courses': len(catalog_codes),
                            'completed_courses': sum(r.get('status') in ('ok', 'no_classes') and not r.get('last_error') for r in records.values()),
                            'courses_with_options': sum(bool(r.get('sections')) for r in records.values())}
        atomic_json(a.output, data)
    def worker(index):
        nonlocal count, failures, total_failures
        with sync_playwright() as pw:
            launch = {'headless': not a.headed}
            if a.browser_path:
                launch['executable_path'] = a.browser_path
            browser = None
            if a.profile:
                context = pw.chromium.launch_persistent_context(str(a.profile), **launch)
            else:
                browser = pw.chromium.launch(**launch)
                context = browser.new_context()
            # Avoid downloading fonts/images/media; keep scripts/styles and all PeopleSoft request data.
            context.route('**/*', lambda route: route.abort() if route.request.resource_type in ('image', 'font', 'media') else route.continue_())
            page = context.new_page()
            client = UNTSearch(page, a.term)
            try:
                if a.headed and a.profile:
                    page.goto(HOME_URL, wait_until='domcontentloaded')
                    input('If sign-in is required, complete it in this browser. Press Enter to start: ')
                    control.start_console()
                while control.wait():
                    try:
                        code = queue.get_nowait()
                    except Empty:
                        return
                    sections, status, message = [], 'scraper_failure', ''
                    for attempt in range(a.retries + 1):
                        if not control.wait():
                            return
                        try:
                            sections, status, message = client.scrape(code)
                            break
                        except Exception as exc:
                            message = str(exc)
                            client.ready = False
                            if 'sign-in gate' in message.lower():
                                break
                            if attempt < a.retries:
                                delay = min(2 ** (attempt + 1), 10)
                                print(f'[{code}] Network/page error; retry {attempt + 1}/{a.retries} in {delay}s.')
                                if control.stop.wait(delay):
                                    return
                    with write_lock:
                        if status == 'scraper_failure' and records.get(code, {}).get('status') in ('ok', 'no_classes'):
                            # Preserve last successful data on failed refresh; retry on next run.
                            records[code]['last_error'] = message
                        else:
                            records[code] = course_payload(code, sections, status, message)
                        count += 1
                        if status in ('ok', 'no_classes'):
                            refresh_pending.discard(code)
                        failures = failures + 1 if status == 'scraper_failure' else 0
                        total_failures += int(status == 'scraper_failure')
                        checkpoint()
                        elapsed = time.monotonic() - started
                        rate = count / max(elapsed / 60, 0.01)
                        remaining = max(0, len(pending) - count)
                        print(f'[{count}/{len(pending)} | {count/len(pending):.1%}] {code}: {status} • {len(sections)} options • {rate:.1f} courses/min • ETA {remaining/max(rate, .01):.1f} min • saved', flush=True)
                        if status == 'scraper_failure':
                            print(f'  {message}', file=sys.stderr)
                        if failures >= 3 or 'sign-in gate' in message.lower():
                            print('Stopping after repeated failures. Check Wi-Fi or site access, then rerun to resume.', file=sys.stderr)
                            control.stop.set()
                    if control.stop.wait(a.delay):
                        return
            finally:
                context.close()
                if browser:
                    browser.close()
    try:
        with RunLock(a.output):
            checkpoint()
            # Don't let the command listener consume the profile initialization prompt.
            if not (a.headed and a.profile):
                control.start_console()
            executor = ThreadPoolExecutor(max_workers=a.workers)
            futures = [executor.submit(worker, i) for i in range(a.workers)]
            try:
                for future in futures:
                    future.result()
            except KeyboardInterrupt:
                control.stop.set()
                print('\nStopping safely; waiting for in-flight pages to finish. Completed records are already saved.')
            except Exception:
                control.stop.set()
                raise
            finally:
                executor.shutdown(wait=True)
                with write_lock:
                    checkpoint()
    except Exception as exc:
        print(f'Scraper stopped: {exc}\nProgress is retained. Install browsers with: python -m playwright install chromium', file=sys.stderr)
        return 1
    print(f'Saved {a.output}. Run the same command to resume. Failed courses are retryable.')
    return 1 if total_failures or count < len(pending) else 0

if __name__ == '__main__':
    raise SystemExit(main())
