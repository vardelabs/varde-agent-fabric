import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts.implement_contracts import ImplementRequest, ImplementArtifact, ContractError, collect_files
from dispatch.gateway_client import GatewayClient, GatewayError, _NoRedirect
from dispatch.remote_runner import RemoteImplementMetadata, run_remote_implement

ROOT = Path(__file__).resolve().parents[1]

class Response:
    status = 200
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, n=-1): return self.body[:n]

class ImplementTests(unittest.TestCase):
    def request(self):
        return ImplementRequest('org/repo', 'codex', 'corr', 'change it', ({'path':'a.txt','content':'before'},), 30)

    def artifact(self, req):
        before = hashlib.sha256(b'before').hexdigest()
        return ImplementArtifact(req.repository, req.worker_kind, req.correlation_id, req.snapshot_sha256, 'codex', 'cluster', ({'path':'a.txt','before_sha256':before,'after_text':'after'}, {'path':'new.txt','before_sha256':None,'after_text':'new'}), 'done', 'completed', None, 12).to_dict()

    def test_contract_and_digest(self):
        req = self.request()
        self.assertEqual(ImplementRequest.from_dict(req.to_dict()).snapshot_sha256, req.snapshot_sha256)
        with self.assertRaises(ContractError): ImplementRequest.from_dict(dict(req.to_dict(), snapshot_sha256='0'*64))
        self.assertEqual(len(req.snapshot_sha256), 64)
        reversed_keys = dict(req.to_dict(), files=[{'content':'before','path':'a.txt'}])
        self.assertEqual(ImplementRequest.from_dict(reversed_keys).snapshot_sha256, req.snapshot_sha256)
        two = ImplementRequest('org/repo','codex','corr','task',
                               ({'path':'b.txt','content':'B'}, {'path':'a.txt','content':'A'}),30)
        reversed_files = dict(two.to_dict(), files=list(reversed(two.to_dict()['files'])))
        self.assertEqual(ImplementRequest.from_dict(reversed_files).snapshot_sha256, two.snapshot_sha256)

    def test_invalid_requests(self):
        req = self.request().to_dict()
        for path in ('/a', '../a', 'a//b', '.git/config', '.GIT/config', '.Git/hooks/pre-commit',
                     'x/.git/y', 'a\\b', 'x\x00y', 'x\ny', 'x\x1by', 'x'*513):
            with self.subTest(path=path), self.assertRaises(ContractError):
                ImplementRequest.from_dict(dict(req, files=[{'path':path,'content':'x'}]))
        for files in ([{'path':'a','content':'x'}]*2, [], [{'path':str(i),'content':'x'} for i in range(51)]):
            with self.assertRaises(ContractError): ImplementRequest.from_dict(dict(req, files=files))
        twins = ['café.py', 'cafe\u0301.py']
        with self.assertRaises(ContractError):
            ImplementRequest.from_dict(dict(req, files=[{'path':p,'content':'x'} for p in twins]))
        for path in ('.GIT/config', 'x\ny'):
            with self.assertRaises(ContractError):
                ImplementArtifact.from_dict(dict(self.artifact(self.request()), changes=[
                    {'path':path,'before_sha256':None,'after_text':'x'}]))
        with self.assertRaises(ContractError):
            ImplementArtifact.from_dict(dict(self.artifact(self.request()), changes=[
                {'path':p,'before_sha256':None,'after_text':'x'} for p in twins]))
        with self.assertRaises(ContractError): ImplementRequest.from_dict(dict(req, task='x'*16385))
        with self.assertRaises(ContractError): ImplementRequest.from_dict(dict(req, files=[{'path':'a','content':'x'*330000}]))

    def test_collect_rejects_symlink_components(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            (root/'a.txt').write_text('hello')
            (root/'link').symlink_to(root/'a.txt')
            (root/'dir').mkdir()
            (root/'dir'/'indirect').symlink_to(root/'a.txt')
            self.assertEqual(collect_files(root, ('a.txt',)), ({'path':'a.txt','content':'hello'},))
            for path in ('link', 'dir/indirect', '../a.txt'):
                with self.assertRaises(ContractError): collect_files(root, (path,))
            os.link(root/'a.txt', root/'hardlink')
            with self.assertRaises(ContractError): collect_files(root, ('a.txt',))

    def test_gateway_success_and_identity_failures(self):
        req = self.request()
        def opener(http_req, timeout):
            self.assertEqual(timeout, req.timeout_seconds + 45)
            self.assertEqual(http_req.full_url, 'https://gateway.example/v1/implementations')
            self.assertEqual(http_req.get_header('Authorization'), 'Bearer secret')
            self.assertEqual(json.loads(http_req.data), req.to_dict())
            return Response(json.dumps(self.artifact(req)).encode())
        client = GatewayClient('https://gateway.example', 'secret', opener=opener)
        self.assertEqual(client.submit_implementation(req).model_output, 'done')
        self.assertEqual(len(client.submit_implementation(req).changes), 2)
        for bad_change in ({'path':'a.txt','before_sha256':None,'after_text':'after'},
                           {'path':'new.txt','before_sha256':'0'*64,'after_text':'new'},
                           {'path':'new.txt','before_sha256':None,'after_text':None}):
            data = dict(self.artifact(req), changes=[bad_change])
            with self.subTest(bad_change=bad_change), self.assertRaises(GatewayError):
                GatewayClient('https://gateway.example', 'secret', opener=lambda *a, **k:Response(json.dumps(data).encode())).submit_implementation(req)
        for field, value in (('repository','other/repo'), ('worker_kind','other'), ('correlation_id','other'), ('snapshot_sha256','0'*64), ('finish_reason','timeout'), ('fallback','local'), ('changes',[{'path':'../bad','before_sha256':None,'after_text':'x'}])):
            data = dict(self.artifact(req), **{field:value})
            client = GatewayClient('https://gateway.example', 'secret', opener=lambda *a, **k:Response(json.dumps(data).encode()))
            with self.subTest(field=field), self.assertRaises(GatewayError): client.submit_implementation(req)
        client = GatewayClient('https://gateway.example', 'secret', opener=lambda *a, **k:Response(b'{' ))
        with self.assertRaises(GatewayError): client.submit_implementation(req)
        client = GatewayClient('https://gateway.example', 'secret', opener=lambda *a, **k:Response(b' '*330000))
        with self.assertRaises(GatewayError): client.submit_implementation(req)
        error = urllib.error.HTTPError('https://gateway.example', 401, 'unauthorized', Message(), None)
        client = GatewayClient('https://gateway.example', 'secret', opener=mock.Mock(side_effect=error))
        with self.assertRaisesRegex(GatewayError, '401'): client.submit_implementation(req)

    def test_implementation_redirects_refused(self):
        import io
        import urllib.request
        from http.client import HTTPMessage
        req = urllib.request.Request('https://gateway.example/v1/implementations', data=b'{}',
                                     headers={'Authorization': 'Bearer secret'})
        import ssl
        for label, client in (('default', GatewayClient('https://gateway.example', 'secret')),
                              ('ca', GatewayClient('https://gateway.example', 'secret',
                                                   ssl_context=ssl.create_default_context()))):
            with self.subTest(opener=label):
                handlers = [item for item in getattr(getattr(client.opener, '__self__'), 'handlers')
                            if isinstance(item, _NoRedirect)]
                self.assertEqual(len(handlers), 1)
                self.assertIsNone(handlers[0].redirect_request(
                    req, io.BytesIO(), 302, 'Found', HTTPMessage(),
                    'https://other.example/steal'))

    def test_remote_error_never_falls_back(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            (Path(directory)/'a.txt').write_text('before')
            meta = RemoteImplementMetadata('org/repo','corr',('a.txt',),'change it',directory,'out.json')
            with mock.patch('dispatch.remote_runner._gateway_client') as client, mock.patch('dispatch.remote_runner._run_local_codex') as local:
                client.return_value.submit_implementation.side_effect = OSError('offline')
                with self.assertRaises(OSError): run_remote_implement(meta, fallback_policy='none')
                with self.assertRaises(GatewayError): run_remote_implement(meta, fallback_policy='remote-unavailable')
                local.assert_not_called()

    def test_create_collision_refused_before_publication(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
            root = Path(directory)
            (root/'a.txt').write_text('before')
            (root/'new.txt').write_text('unselected')
            meta = RemoteImplementMetadata('org/repo','corr',('a.txt',),'change it',directory,str(root/'out.json'))
            with mock.patch('dispatch.remote_runner._gateway_client') as client:
                client.return_value.submit_implementation.side_effect = lambda req: ImplementArtifact.from_dict(self.artifact(req))
                with self.assertRaisesRegex(GatewayError, 'already exists'):
                    run_remote_implement(meta, fallback_policy='none')
            self.assertEqual((root/'new.txt').read_text(), 'unselected')

    def test_cli_routes_implementation_and_rejects_review_fields(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            (root/'a.txt').write_text('before')
            route = root/'routes.json'
            route.write_text(json.dumps({'schema':'dispatch-routes.v1','routes':{'model':{
                'default_harness':'codex','target':'remote','kinds':['implement'],
                'fallback':{'policy':'none'}}}}))
            command = [str(ROOT/'dispatch/cli/varde-agent-dispatch'), '--model','model','--routes',str(route),
                       '--kind','implement','--task','change it','--workdir',directory,'--repository','org/repo',
                       '--correlation-id','corr','--path','a.txt','--output',str(root/'artifact.json')]
            env = {**os.environ, 'TMPDIR':directory, 'DISPATCH_GATEWAY_URL':'https://127.0.0.1:1',
                   'DISPATCH_GATEWAY_TOKEN':'dummy'}
            for extra in (['--pull-request','7'], ['--head-sha','a'*40], ['--base-sha','b'*40],
                          ['--diff',str(root/'a.txt')], ['--diff',''], ['--pull-request',''],
                          ['--path','../bad']):
                result = subprocess.run(command + extra, env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, extra)
                self.assertFalse((root/'artifact.json').exists())
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root/'artifact.json').exists())
            self.assertEqual((root/'a.txt').read_text(), 'before')

    def test_runner_main_writes_validated_artifact_without_modifying_sources(self):
        from dispatch.remote_runner import main
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            (root/'a.txt').write_text('before')
            output = root/'artifact.json'
            argv = ['remote_runner.py','--output',str(output),'--local-backend','/bin/false',
                    '--harness','codex','--target','remote','--fallback-policy','none','--model','model',
                    '--workdir',directory,'--repository','org/repo','--correlation-id','corr',
                    '--kind','implement','--path','a.txt','--task','change it']
            def submit(request): return ImplementArtifact.from_dict(self.artifact(request))
            with mock.patch.object(sys,'argv',argv), mock.patch('dispatch.remote_runner._gateway_client') as client:
                client.return_value.submit_implementation.side_effect = submit
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.read_text())['schema'], 'implement-artifact.v1')
            self.assertEqual((root/'a.txt').read_text(), 'before')

    def test_runner_main_no_changes_preserves_artifact_with_distinct_status(self):
        from dispatch.remote_runner import main
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
            root = Path(directory)
            (root/'a.txt').write_text('before')
            output = root/'artifact.json'
            argv = ['remote_runner.py','--output',str(output),'--local-backend','/bin/false',
                    '--harness','codex','--target','remote','--fallback-policy','none','--model','model',
                    '--workdir',directory,'--repository','org/repo','--correlation-id','corr',
                    '--kind','implement','--path','a.txt','--task','change it']
            def submit(req):
                return ImplementArtifact.from_dict(dict(self.artifact(req), changes=[]))
            with mock.patch.object(sys,'argv',argv), mock.patch('dispatch.remote_runner._gateway_client') as client:
                client.return_value.submit_implementation.side_effect = submit
                self.assertEqual(main(), 6)
            self.assertEqual(json.loads(output.read_text())['changes'], [])
            self.assertEqual((root/'a.txt').read_text(), 'before')

if __name__ == '__main__': unittest.main()
