import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cloud_data import publish, validate

class PublicationTests(unittest.TestCase):
    def test_incomplete_refresh_cannot_replace_publication(self):
        with self.assertRaises(ValueError):
            validate({'term': '2027 Spring', 'courses': {'MATH 1680': {'status': 'ok', 'sections': [{}], 'last_error': 'offline'}}}, ['MATH 1680'])
        with self.assertRaises(ValueError):
            validate({'term': '2026 Fall', 'courses': {}})
        with self.assertRaises(ValueError):
            validate({'term': '2027 Spring', 'refresh_pending': ['MATH 1680'], 'courses': {'MATH 1680': {'status': 'ok', 'sections': [{}]}}}, ['MATH 1680'])

    def test_manifest_written_last_and_not_on_upload_failure(self):
        data = {'term': '2027 Spring', 'courses': {'MATH 1680': {'status': 'ok', 'sections': [{'linked_sections': [{'instructor': 'Lab Professor'}]}]}}}
        class Client:
            def __init__(self, fail=False): self.keys, self.fail = [], fail
            def put_object(self, **kwargs):
                if self.fail and kwargs['Key'].endswith('index.json'): raise RuntimeError('offline')
                self.keys.append(kwargs['Key'])
        client = Client()
        publish(client, 'private', data, {}, ['MATH 1680'])
        self.assertEqual(client.keys[-1], 'published/spring-2027/manifest.json')
        client = Client(True)
        with self.assertRaises(RuntimeError): publish(client, 'private', data, {}, ['MATH 1680'])
        self.assertNotIn('published/spring-2027/manifest.json', client.keys)

class FallPublicationTests(unittest.TestCase):
    def test_fall_paths_are_isolated_and_wrong_term_rejected(self):
        class Client:
            def __init__(self): self.keys=[]
            def put_object(self, **kw): self.keys.append(kw['Key'])
        data={'term':'2026 Fall','courses':{'MATH 1680':{'status':'ok','sections':[{}]}}}
        c=Client()
        publish(c,'private',data,{},['MATH 1680'],'fall-2026')
        self.assertEqual(c.keys[-1],'published/fall-2026/manifest.json')
        self.assertTrue(all('spring-2027' not in k for k in c.keys))
        with self.assertRaises(ValueError): publish(c,'private',data,{},['MATH 1680'])

    def test_cli_checkpoints_fall_without_touching_spring_key(self):
        import tempfile, json, types, os
        from unittest.mock import patch
        import cloud_data
        writes=[]
        client=types.SimpleNamespace(put_object=lambda **kw:writes.append(kw['Key']))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fall.json'
            path.write_text(json.dumps({'term':'2026 Fall','courses':{}}))
            with patch.dict(sys.modules,{'boto3':types.SimpleNamespace(client=lambda *a,**kw:client),'botocore.exceptions':types.SimpleNamespace(ClientError=RuntimeError)}), patch.dict(os.environ,{'R2_ENDPOINT':'https://example.invalid','R2_BUCKET':'test'}), patch.object(sys,'argv',['cloud_data.py','checkpoint','--dataset','fall-2026','--file',str(path)]):
                cloud_data.main()
        self.assertEqual(writes,['checkpoints/fall-2026.json'])
