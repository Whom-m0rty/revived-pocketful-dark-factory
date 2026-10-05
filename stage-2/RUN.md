# Pocketful — stage 2

Build and start the service (listens on `PORT`, default 8080):

```sh
docker build -t pocketful-s2 . && docker run --rm -e PORT=8080 -p 8080:8080 pocketful-s2
```

Check it is up: `curl http://localhost:8080/health` → `{"status":"ok"}`. Open http://localhost:8080/login in a browser for the wallet screens.

State is kept in memory; seed it with `POST /_test/reset`. No network is needed at run time.
