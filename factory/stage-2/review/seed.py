"""Seed a rich fixture for design review and print tokens: ada (full data, holds) and eve (empty)."""
import sys, httpx, datetime as dt
base = sys.argv[1]
now = dt.datetime.now(dt.timezone.utc)
iso = lambda d: (now + d).replace(microsecond=0).isoformat()
U = lambda h, n, b: {"id": f"u_{h}", "email": f"{h}@example.com", "password": "password123", "display_name": n, "handle": h, "balance": b}
fx = {"currency": "EUR", "minor_units": 2, "authorization_ttl_seconds": 600,
  "users": [U("ada", "Ada Lovelace", 10000), U("bob", "Bob Marley", 5000), U("cleo", "Cleopatra Philopator", 8000), U("dan", "Dan", 3000), U("eve", "Eve", 2500)],
  "payments": [
    {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_dan", "amount": 1200, "note": "Rent share", "visibility": "private"},
    {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_cleo", "amount": 720, "note": "", "visibility": "public"},
    {"id": "p_3", "from_user_id": "u_cleo", "to_user_id": "u_ada", "amount": 4250, "note": "Concert tickets for the whole group on Saturday night", "visibility": "public"}],
  "requests": [
    {"id": "r_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "Taxi home", "status": "pending"},
    {"id": "r_2", "requester_id": "u_cleo", "payer_id": "u_ada", "amount": 850, "note": "Coffee beans", "status": "declined"},
    {"id": "r_3", "requester_id": "u_ada", "payer_id": "u_cleo", "amount": 4250, "note": "Concert tickets", "status": "pending"},
    {"id": "r_4", "requester_id": "u_ada", "payer_id": "u_bob", "amount": 600, "note": "Parking", "status": "cancelled"}],
  "authorizations": [
    {"id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 2000, "note": "deposit", "visibility": "public", "status": "open", "expires_at": iso(dt.timedelta(hours=2))},
    {"id": "a_2", "from_user_id": "u_cleo", "to_user_id": "u_ada", "amount": 3000, "note": "Bike rental", "visibility": "private", "status": "open", "expires_at": iso(dt.timedelta(hours=3))},
    {"id": "a_3", "from_user_id": "u_ada", "to_user_id": "u_cleo", "amount": 1000, "note": "Tickets", "visibility": "private", "status": "voided", "expires_at": iso(dt.timedelta(hours=-2))},
    {"id": "a_4", "from_user_id": "u_dan", "to_user_id": "u_ada", "amount": 1500, "note": "Car share", "visibility": "private", "status": "expired", "expires_at": iso(dt.timedelta(hours=-5))}]}
r = httpx.post(base + "/_test/reset", json=fx); r.raise_for_status()
for h in ("ada", "eve"):
    t = httpx.post(base + "/auth/login", json={"email": f"{h}@example.com", "password": "password123"}).json()["token"]
    print(f"{h}={t}")
