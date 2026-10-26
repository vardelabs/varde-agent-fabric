"""Test doubles for registered dispatch backend adapters."""
from __future__ import annotations

import contextlib
from dataclasses import replace
from collections.abc import Iterator
from unittest import mock

from dispatch import adapters


@contextlib.contextmanager
def patch_local_review(harness: str, **mock_kwargs: object) -> Iterator[mock.Mock]:
    """Replace the registered local review fallback runner of one backend with a mock."""
    local = mock.Mock(**mock_kwargs)
    adapter = adapters.ADAPTERS[harness]
    fallback = replace(adapter.local_review_fallback, run=local)
    with mock.patch.dict(adapters.ADAPTERS, {harness: replace(adapter, local_review_fallback=fallback)}):
        yield local
