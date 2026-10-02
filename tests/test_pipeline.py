import unittest
import tempfile
from pathlib import Path
from class_scraper import parse_class_table, parse_days_and_times
from catalog_scraper import parse_page, page_url
from scraper_common import atomic_json, load_json
from bs4 import BeautifulSoup

class PipelineTests(unittest.TestCase):
    def test_paired_options_keep_both_classes(self):
        options = parse_class_table(Path('tests/fixtures/paired-sections.html').read_text())
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['class_numbers'], ['6862', '6864'])
        self.assertEqual([m['days'] for m in options[0]['meetings']], ['TR', 'M'])
        self.assertEqual(options[0]['linked_sections'][1]['open_seats'], 33)
        self.assertEqual(options[1]['status'], 'Closed')
    def test_error_page_not_empty_course(self):
        with self.assertRaises(RuntimeError):
            parse_class_table('<h1>403 Forbidden</h1>')
    def test_truncated_options_not_completed(self):
        html=Path('tests/fixtures/paired-sections.html').read_text().replace('2 options', '20 options')
        with self.assertRaises(RuntimeError):
            parse_class_table(html)
    def test_weekend_and_dash_times(self):
        self.assertEqual(parse_days_and_times('Saturday Sunday 11:00 AM - 12:10 PM'), ('SU','11:00AM','12:10PM'))
    def test_catalog_pagination_and_unicode(self):
        html='<a href="preview_course_nopop.php?catoid=40&amp;coid=1">MATH 1680\u00a0-\u00a0Elementary Statistics</a><a href="content.php?catoid=40&amp;navoid=4629&amp;filter%5Bcpage%5D=40">40</a><li>FAKE 1234 - unrelated text</li>'
        records,last=parse_page(html,'undergraduate',page_url('undergraduate',1))
        self.assertEqual(last,40);self.assertEqual(len(records),1)
        self.assertEqual(records[0]['course_name'],'Elementary Statistics')
    def test_catalog_error_not_end(self):
        with self.assertRaises(RuntimeError):
            parse_page('<h1>Forbidden</h1>','graduate',page_url('graduate',1))
    def test_checkpoint_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'data.json';atomic_json(p,{'courses':{'MATH 1680':{'status':'ok'}}})
            self.assertEqual(load_json(p)['courses']['MATH 1680']['status'],'ok')
    def test_arranged_row_missing_room_does_not_shift_instructor_into_room(self):
        soup=BeautifulSoup(Path('tests/fixtures/paired-sections.html').read_text(), 'html.parser')
        row=soup.select('tr')[2]
        cells=row.find_all('td',recursive=False)
        cells[6].extract()
        row.append(soup.new_tag('td'))
        option=parse_class_table(str(soup))[1]
        self.assertEqual(option['room'],'No Facility Assigned')
        self.assertEqual(option['instructor'],'Schwaighofer,Benjamin Aaron')
        self.assertEqual(option['total_seats'],60)
    def test_multiple_meetings_keep_their_own_rooms(self):
        soup=BeautifulSoup(Path('tests/fixtures/paired-sections.html').read_text(), 'html.parser')
        cells=soup.select('tr')[2].find_all('td',recursive=False)
        cells[5].clear();cells[5].append('Monday 3:30PM to 4:50PM Wednesday 2:25PM to 5:15PM')
        cells[6].clear();cells[6].append('SAGE 356 Wh 317')
        option=parse_class_table(str(soup))[1]
        self.assertEqual([m['room'] for m in option['meetings']],['SAGE 356','Wh 317'])
        self.assertEqual([m['days'] for m in option['meetings']],['M','W'])

if __name__=='__main__': unittest.main()
