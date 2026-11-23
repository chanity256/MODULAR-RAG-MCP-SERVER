"""PI3: TreeSearchFactory 路由单测（离线）。"""

from __future__ import annotations

import pytest

from core.settings import TreeSearchConfig
from libs.tree_search.fake_backend import FakeTreeSearch
from libs.tree_search.http_backend import PageIndexHttpBackend
from libs.tree_search.tree_search_factory import (
    TreeSearchBackendRouteError,
    TreeSearchFactory,
)


class TestTreeSearchFactory:
    def test_production_and_fake_backends_registered(self):
        assert TreeSearchFactory.is_registered("pageindex_http")
        assert TreeSearchFactory.is_registered("fake")

    def test_available_backends_sorted(self):
        assert TreeSearchFactory.available_backends() == ["fake", "pageindex_http"]

    def test_create_routes_to_http_backend(self):
        settings = type("S", (), {"tree_search": TreeSearchConfig()})()
        backend = TreeSearchFactory.create(settings)
        assert isinstance(backend, PageIndexHttpBackend)
        assert backend.backend_name == "pageindex_http"

    def test_create_routes_to_fake_backend(self):
        settings = type(
            "S", (), {"tree_search": TreeSearchConfig(backend="fake")}
        )()
        backend = TreeSearchFactory.create(settings)
        assert isinstance(backend, FakeTreeSearch)

    def test_unknown_backend_raises(self):
        settings = type(
            "S", (), {"tree_search": TreeSearchConfig(backend="nope")}
        )()
        with pytest.raises(TreeSearchBackendRouteError, match="unknown tree_search backend"):
            TreeSearchFactory.create(settings)
