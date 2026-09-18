"""pytest 公共夹具：所有接口测试都落到临时 SQLite，绝不触碰真实数据库。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app, get_conn


@pytest.fixture()
def client(tmp_path):
    """TestClient + 本次用例独占的临时数据库（(client, db_path)）。"""
    db_path = tmp_path / "history.db"

    def _get_conn():
        conn = storage.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_conn] = _get_conn
    with TestClient(app) as c:
        yield c, db_path
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _isolate_default_database(tmp_path, monkeypatch):
    """未显式使用 client 夹具的旧测试（如 test_api 模块级 client）
    也重定向到临时库，避免在源码目录生成 data/history.db。"""
    db_path = tmp_path / "default-history.db"

    def _get_conn():
        conn = storage.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_conn] = _get_conn
    yield
    app.dependency_overrides.clear()
