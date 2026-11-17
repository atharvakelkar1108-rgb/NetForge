import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as netforge
from netcore import store


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        store.DB = self.tmp.name
        netforge.FAILS.clear()
        netforge.app.config["TESTING"] = True
        self.c = netforge.app.test_client()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def register(self, client=None, name="alice", pw="secret1", confirm=None):
        return (client or self.c).post("/register", data={"username": name, "password": pw, "confirm": confirm or pw})

    # ---- access control --------------------------------------------------
    def test_home_is_public(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/login").status_code, 200)

    def test_interface_requires_login(self):
        r = self.c.get("/app")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_api_requires_login(self):
        for path in ("/api/validate", "/api/ping", "/api/configs"):
            self.assertEqual(self.c.post(path, json={}).status_code, 401, path)
        self.assertEqual(self.c.get("/api/templates").status_code, 401)
        self.assertEqual(self.c.get("/api/projects").status_code, 401)

    # ---- registration -----------------------------------------------------
    def test_register_logs_in_and_opens_interface(self):
        r = self.register()
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/app"))
        self.assertEqual(self.c.get("/app").status_code, 200)
        self.assertEqual(self.c.get("/api/templates").status_code, 200)

    def test_register_rejects_bad_input(self):
        self.assertEqual(self.register(name="a b").status_code, 400)               # bad characters
        self.assertEqual(self.register(name="ab").status_code, 400)                # too short
        self.assertEqual(self.register(pw="123").status_code, 400)                 # weak password
        self.assertEqual(self.register(pw="secret1", confirm="secret2").status_code, 400)

    def test_username_unique_case_insensitive(self):
        self.register(name="Alice")
        other = netforge.app.test_client()
        self.assertEqual(self.register(other, name="alice").status_code, 400)

    def test_password_is_hashed(self):
        self.register()
        self.assertNotEqual(store.get_user_by_name("alice")["pw_hash"], "secret1")

    # ---- login / logout ---------------------------------------------------
    def test_login_wrong_and_right_password(self):
        self.register()
        c2 = netforge.app.test_client()
        bad = c2.post("/login", data={"username": "alice", "password": "nope"})
        self.assertEqual(bad.status_code, 401)
        self.assertIn(b"Incorrect username or password", bad.data)
        unknown = c2.post("/login", data={"username": "ghost", "password": "nope"})
        self.assertIn(b"Incorrect username or password", unknown.data)            # same message: no user enumeration
        ok = c2.post("/login", data={"username": "ALICE", "password": "secret1"})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(c2.get("/app").status_code, 200)

    def test_logout_ends_session(self):
        self.register()
        self.c.post("/logout")
        self.assertEqual(self.c.get("/app").status_code, 302)
        self.assertEqual(self.c.post("/api/validate", json={}).status_code, 401)

    def test_login_redirects_back_to_next(self):
        self.register()
        c2 = netforge.app.test_client()
        r = c2.post("/login", data={"username": "alice", "password": "secret1", "next": "/app"})
        self.assertTrue(r.headers["Location"].endswith("/app"))

    def test_open_redirect_is_blocked(self):
        self.register()
        for evil in ("//evil.com", "https://evil.com", "/\\evil.com"):
            c2 = netforge.app.test_client()
            r = c2.post("/login", data={"username": "alice", "password": "secret1", "next": evil})
            self.assertTrue(r.headers["Location"].endswith("/app"), evil)

    def test_brute_force_is_throttled(self):
        self.register()
        c2 = netforge.app.test_client()
        for _ in range(netforge.MAX_FAILS):
            c2.post("/login", data={"username": "alice", "password": "wrong"})
        r = c2.post("/login", data={"username": "alice", "password": "secret1"})   # even the right password is refused
        self.assertEqual(r.status_code, 429)

    # ---- per-user projects ------------------------------------------------
    def test_projects_are_private_per_user(self):
        self.register(name="alice")
        topo = {"nodes": [{"id": "n1", "type": "host", "hostname": "H1", "interfaces": []}], "links": []}
        self.assertEqual(self.c.post("/api/projects", json={"name": "lab", "topology": topo}).status_code, 200)
        self.assertEqual(len(self.c.get("/api/projects").get_json()), 1)
        bob = netforge.app.test_client()
        self.register(bob, name="bob")
        self.assertEqual(bob.get("/api/projects").get_json(), [])
        self.assertEqual(bob.get("/api/projects/lab").status_code, 404)
        bob.delete("/api/projects/lab")                                           # must not delete alice's project
        self.assertEqual(self.c.get("/api/projects/lab").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)