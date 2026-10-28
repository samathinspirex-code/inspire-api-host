import unittest
from unittest.mock import patch

from app.core.errors import ValidationError
from app.modules.lms import zoom_service


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def first(self):
        return self.value


class _Database:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, params):
        sql = str(statement)
        self.statements.append((sql, params))
        if "DELETE FROM lms_zoom_oauth_states" in sql:
            return _Result(7)
        if "SELECT zoom_user_id,email" in sql:
            return _Result({"zoom_user_id": "zoom-host-1", "email": "host@example.com"})
        return _Result()

    async def commit(self):
        pass


class _Response:
    is_error = False

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Client:
    def __init__(self, zoom_user_id):
        self.zoom_user_id = zoom_user_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def post(self, *_args, **_kwargs):
        return _Response({"access_token": "access", "refresh_token": "refresh", "expires_in": 3600})

    async def get(self, *_args, **_kwargs):
        return _Response({"id": self.zoom_user_id, "email": "host@example.com", "account_id": "account-1"})


class ZoomHostReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_host_updates_tokens_without_replacing_connection(self):
        db = _Database()
        with patch.object(zoom_service.httpx, "AsyncClient", return_value=_Client("zoom-host-1")), \
             patch.object(zoom_service, "_encrypt", side_effect=lambda value: value):
            email = await zoom_service.complete_connection(db, "code", "relink:12:random-state")

        self.assertEqual(email, "host@example.com")
        upsert = next((sql, params) for sql, params in db.statements if "INSERT INTO lms_zoom_host_connections" in sql)
        self.assertIn("ON CONFLICT(zoom_user_id) DO UPDATE", upsert[0])
        self.assertNotIn("capacity=", upsert[0])
        self.assertNotIn("connection_id=", upsert[0])
        self.assertEqual(upsert[1]["zoom_user"], "zoom-host-1")

    async def test_different_zoom_login_cannot_replace_selected_host(self):
        db = _Database()
        with patch.object(zoom_service.httpx, "AsyncClient", return_value=_Client("another-user")):
            with self.assertRaises(ValidationError):
                await zoom_service.complete_connection(db, "code", "relink:12:random-state")

        self.assertFalse(any("INSERT INTO lms_zoom_host_connections" in sql for sql, _ in db.statements))


if __name__ == "__main__":
    unittest.main()
