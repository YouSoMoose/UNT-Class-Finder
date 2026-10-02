"""Private R2 checkpoint transfer and validated, atomic release publication."""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scraper'))
from scraper_common import atomic_json, load_json
from urllib.parse import quote

TERM = '2027 Spring'
KEY = 'checkpoints/spring-2027.json'

def validate(data, codes=None, term=TERM):
    if data.get('term') != term or not isinstance(data.get('courses'), dict):
        raise ValueError('Wrong term or invalid checkpoint schema')
    if codes is not None:
        if data.get('refresh_pending'):
            raise ValueError('Refresh cycle still pending: public release remains unchanged')
        if not codes or any(data['courses'].get(code, {}).get('status') not in ('ok', 'no_classes') or data['courses'][code].get('last_error') for code in codes):
            raise ValueError('Incomplete/failed scrape: public release remains unchanged')
        for code in codes:
            record = data['courses'][code]
            if not isinstance(record.get('sections'), list) or (record['status'] == 'ok' and not record['sections']) or (record['status'] == 'no_classes' and record['sections']):
                raise ValueError(f'Invalid sections for {code}')

def publish(client, bucket, data, titles, codes, dataset="spring-2027"):
    validate(data, codes, "2026 Fall" if dataset == "fall-2026" else TERM)
    release = uuid.uuid4().hex
    prefix = f'releases/{dataset}/{release}'
    def put(key, value):
        client.put_object(Bucket=bucket, Key=key, Body=json.dumps(value).encode(), ContentType='application/json')
    index = []
    for code in sorted(codes):
        record = data['courses'][code]
        put(f'{prefix}/courses/{quote(code, safe="")}.json', {'code': code, 'title': titles.get(code, ''), **record})
        instructors = sorted({s.get('instructor', '') for s in record['sections']} | {p.get('instructor', '') for s in record['sections'] for p in s.get('linked_sections', [])})
        index.append({'code': code, 'title': titles.get(code, ''), 'instructors': instructors, 'has_sections': bool(record['sections'])})
    put(f'{prefix}/index.json', index)
    # Only switch readers after every object is uploaded successfully.
    put(f'published/{dataset}/manifest.json', {'release': release, 'updated_at': data.get('updated_at')})

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['restore', 'checkpoint', 'publish'])
    parser.add_argument('--dataset', choices=['spring-2027', 'fall-2026'], default='fall-2026')
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    args.file = args.file or Path(f"private-data/{args.dataset}.json")
    term = "2026 Fall" if args.dataset == "fall-2026" else TERM
    key = f"checkpoints/{args.dataset}.json"
    import boto3
    from botocore.exceptions import ClientError
    client = boto3.client('s3', endpoint_url=os.environ['R2_ENDPOINT'], region_name='auto')
    bucket = os.environ['R2_BUCKET']
    if args.action == 'restore':
        try:
            data = json.loads(client.get_object(Bucket=bucket, Key=key)['Body'].read())
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('NoSuchKey', '404'):
                raise
            print('No remote checkpoint; using local seed if present.')
            return
        validate(data, term=term)
        atomic_json(args.file, data)
    else:
        data = load_json(args.file)
        validate(data, term=term)
        if args.action == 'checkpoint':
            client.put_object(Bucket=bucket, Key=key, Body=json.dumps(data).encode(), ContentType='application/json')
        else:
            from class_scraper import load_course_codes
            root = Path('.')
            publish(client, bucket, data, load_json(root / 'public/data/catalog-index.json'), load_course_codes(root / 'catalog_courses.json'), args.dataset)

if __name__ == '__main__':
    main()

