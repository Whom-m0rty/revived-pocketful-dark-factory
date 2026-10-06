#!/bin/sh
# Demo data used in the video: ada (80.00 EUR available, 20.00 on hold), bob, cy.
# Start stage-4 (see stage-4/RUN.md), then: sh docs/seed_demo.sh   — log in as ada@example.com / "correct horse".
EXP=$(date -u -d '+2 hours' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v+2H +%Y-%m-%dT%H:%M:%SZ)   # hold expires in 2 h (GNU or BSD date)
curl -X POST ${BASE:-http://localhost:8080}/_test/reset -H 'Content-Type: application/json' -d '{
 "currency":"EUR","minor_units":2,"authorization_ttl_seconds":600,
 "users":[
  {"id":"u_ada","email":"ada@example.com","password":"correct horse","display_name":"Ada","handle":"ada","balance":10000},
  {"id":"u_bob","email":"bob@example.com","password":"correct horse","display_name":"Bob","handle":"bob","balance":2500},
  {"id":"u_cy","email":"cy@example.com","password":"correct horse","display_name":"Cy","handle":"cy","balance":500}],
 "payments":[
  {"id":"p_1","from_user_id":"u_bob","to_user_id":"u_ada","amount":1250,"note":"Pizza night","visibility":"public"},
  {"id":"p_2","from_user_id":"u_ada","to_user_id":"u_cy","amount":300,"note":"Coffee","visibility":"private"}],
 "requests":[
  {"id":"r_1","requester_id":"u_bob","payer_id":"u_ada","amount":1200,"note":"Taxi home","status":"pending"},
  {"id":"r_2","requester_id":"u_ada","payer_id":"u_cy","amount":400,"note":"Concert tickets","status":"pending"}],
 "authorizations":[
  {"id":"a_1","from_user_id":"u_ada","to_user_id":"u_bob","amount":2000,"note":"Bike deposit","visibility":"public","status":"open","expires_at":"'"$EXP"'"}]}'
echo
