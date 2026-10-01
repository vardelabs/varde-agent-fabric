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
        with self.assertRaises(ContractError):
            ImplementRequest('o/r', 'opencode', 'id', 'edit', legacy.files, 3, model='other/model')
