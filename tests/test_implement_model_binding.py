import unittest
from contracts.implement_contracts import ImplementRequest, ImplementArtifact, ContractError

MODELS = ('opencode-go/kimi-k2.7-code', 'opencode-go/kimi-k3')

class ModelBindingTests(unittest.TestCase):
    def test_transport_rejects_other_model_location_and_fallback(self):
        import json
        import io
        from dispatch.gateway_client import GatewayClient, GatewayError
        for model in MODELS:
            req = ImplementRequest('o/r', 'opencode', 'id', 'edit', ({'path':'a','content':'old'},), 3, model=model)
            good = ImplementArtifact('o/r', 'opencode', 'id', req.snapshot_sha256, model,
                'cluster', (), 'done', 'completed', None, 0, model=model).to_dict()
            def submit(payload):
                class Response(io.BytesIO):
                    status = 200
                return GatewayClient('https://gateway.example','synthetic',
                    opener=lambda *args, **kwargs: Response(json.dumps(payload).encode())).submit_implementation(req)
            self.assertEqual(submit(good).model, model)
            other = MODELS[1-MODELS.index(model)]
            for bad in ({**good,'model':other,'provider':other}, {**good,'execution_location':'requester'},
                        {**good,'fallback':{'worker':'codex'}}, {**good,'finish_reason':'incomplete'},
                        {**good,'provider':'other/provider'}):
                with self.assertRaises(GatewayError): submit(bad)

    def test_versioned_model_binding_and_codex_compatibility(self):
        legacy = ImplementRequest('o/r', 'codex', 'id', 'edit', ({'path':'a', 'content':'old'},), 3)
        self.assertEqual(ImplementRequest.from_dict(legacy.to_dict()), legacy)
        self.assertEqual(legacy.to_dict()['schema'], 'implement-request.v1')
        for model in MODELS:
            with self.subTest(model=model):
                req = ImplementRequest('o/r', 'opencode', 'id', 'edit', legacy.files, 3, model=model)
                self.assertEqual(req.to_dict()['schema'], 'implement-request.v2')
                self.assertEqual(ImplementRequest.from_dict(req.to_dict()), req)
                artifact = ImplementArtifact('o/r', 'opencode', 'id', req.snapshot_sha256, model,
                    'cluster', (), 'done', 'completed', None, 0, model=model)
                self.assertEqual(ImplementArtifact.from_dict(artifact.to_dict()), artifact)
                with self.assertRaises(ContractError):
                    ImplementArtifact('o/r', 'opencode', 'id', req.snapshot_sha256, MODELS[1-MODELS.index(model)],
                        'cluster', (), 'done', 'completed', None, 0, model=model)
                wrong = req.to_dict(); wrong['schema'] = 'implement-request.v1'
                with self.assertRaises(ContractError): ImplementRequest.from_dict(wrong)
        for bad in ('model', 'opencode-go/', 'opencode-go/Kimi', 'opencode-go/kimi k3',
                    'opencode-go/../x', 'opencode-go/-x', 'opencode-go/' + 'a' * 65, None,
                    '/kimi-k3', 'Provider/kimi-k3', 'a/b/c/d', 'p' * 33 + '/kimi-k3', 'p/v//m', 'p/' + 'v' * 33 + '/m'):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                ImplementRequest('o/r', 'opencode', 'id', 'edit', legacy.files, 3, model=bad)

    def test_a_new_opencode_go_model_needs_no_fabric_release(self):
        legacy = ImplementRequest('o/r', 'codex', 'id', 'edit', ({'path':'a', 'content':'old'},), 3)
        req = ImplementRequest('o/r', 'opencode', 'id', 'edit', legacy.files, 3, model='opencode-go/glm-5.1')
        self.assertEqual(ImplementRequest.from_dict(req.to_dict()).model, 'opencode-go/glm-5.1')

    def test_a_new_opencode_provider_needs_no_fabric_release(self):
        req = ImplementRequest('o/r', 'opencode', 'id', 'edit', ({'path':'a', 'content':'old'},), 3,
                               model='other-provider/kimi-k3')
        self.assertEqual(ImplementRequest.from_dict(req.to_dict()).model, 'other-provider/kimi-k3')
        vendor = ImplementRequest('o/r', 'opencode', 'id', 'edit', ({'path':'a', 'content':'old'},), 3,
                                  model='aggregator/vendor/model-1.0')
        self.assertEqual(ImplementRequest.from_dict(vendor.to_dict()).model, 'aggregator/vendor/model-1.0')


class RecordedFallbackTests(unittest.TestCase):
    MODEL = 'opencode-go/kimi-k3'

    def artifact(self, provider, fallback):
        req = ImplementRequest('o/r', 'opencode', 'id', 'edit', ({'path':'a','content':'old'},), 3, model=self.MODEL)
        return req, ImplementArtifact('o/r', 'opencode', 'id', req.snapshot_sha256, provider,
            'cluster', (), 'done', 'completed', fallback, 0, model=self.MODEL)

    def test_a_sibling_served_job_round_trips_and_names_both_models(self):
        _, artifact = self.artifact('sibling-model', {'from': self.MODEL, 'reason': 'rate-limit'})
        restored = ImplementArtifact.from_dict(artifact.to_dict())
        self.assertEqual((restored.provider, restored.fallback), ('sibling-model', {'from': self.MODEL, 'reason': 'rate-limit'}))

    def test_a_fallback_must_be_well_formed(self):
        for provider, fallback in (
                ('sibling-model', {'from': 'other/model', 'reason': 'rate-limit'}),
                ('sibling-model', {'from': self.MODEL, 'reason': 'timeout'}),
                ('sibling-model', {'from': self.MODEL, 'reason': ['rate-limit']}),
                ('sibling-model', {'from': self.MODEL, 'reason': {'rate-limit': 1}}),
                ('sibling-model', {'from': [self.MODEL], 'reason': 'rate-limit'}),
                ('sibling-model', {'from': self.MODEL}),
                ('sibling-model', {'from': self.MODEL, 'reason': 'rate-limit', 'extra': 1}),
                (self.MODEL, {'from': self.MODEL, 'reason': 'rate-limit'}),
                ('sibling-model', 'local'),
                ('sibling-model', None)):
            with self.subTest(provider=provider, fallback=fallback), self.assertRaises(ContractError):
                self.artifact(provider, fallback)

    def test_a_fallback_needs_a_model_bound_request(self):
        legacy = ImplementRequest('o/r', 'codex', 'id', 'edit', ({'path':'a','content':'old'},), 3)
        with self.assertRaises(ContractError):
            ImplementArtifact('o/r', 'codex', 'id', legacy.snapshot_sha256, 'sibling-model', 'cluster', (), 'done',
                              'completed', {'from': 'codex', 'reason': 'rate-limit'}, 0)

    def test_the_gateway_client_accepts_a_recorded_fallback(self):
        import io
        import json
        from dispatch.gateway_client import GatewayClient
        req, artifact = self.artifact('sibling-model', {'from': self.MODEL, 'reason': 'rate-limit'})

        class Response(io.BytesIO):
            status = 200
        client = GatewayClient('https://gateway.example', 'synthetic',
                               opener=lambda *args, **kwargs: Response(json.dumps(artifact.to_dict()).encode()))
        self.assertEqual(client.submit_implementation(req).provider, 'sibling-model')
