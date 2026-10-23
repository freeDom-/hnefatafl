"""Legacy regression clients use real login, sessions and CSRF; no app bypass."""

from fastapi.testclient import TestClient as RawClient

PASSWORD = "fixture-only-password-123"


class AuthenticatedClient(RawClient):
    def __init__(self, app, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        auth = app.state.auth
        with auth.store.connection() as c:
            exists = c.execute("SELECT 1 FROM accounts WHERE username='Odin'").fetchone()
        if not exists:
            auth.create_account("Odin", "odin@example.test", PASSWORD, "ADMIN", bootstrap=True)
        result = self.post(
            "/api/auth/login",
            json={"identity": "Odin", "password": PASSWORD},
            headers={"Origin": "http://testserver"},
        )
        assert result.status_code == 200, result.text
        self.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        self.headers["Origin"] = "http://testserver"
