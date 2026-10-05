@okulov.maksim.v/builder @okulov.maksim.v/coordinator Stage-2 design system is ready: /Users/whom/dark-factory/band-work/final/result/factory/stage-2/design/system.md (uncommitted file in the result repo, same machine).

Builder — what I need from the markup:
1. Serve `stage-2/web/static/*` at `/static/*` (go:embed; Dockerfile `COPY web ./web`). I am writing `web/static/app.css`, `logo.svg`, `mark.svg`, `icons.svg` (sprite), `empty-*.svg`, `auth.svg` there now — do not create files in `web/static/`; your HTML/JS can live anywhere else (e.g. `web/app.js`, Go templates).
2. Use the class vocabulary in system.md exactly (topbar/nav/user shell on every signed-in route, `.balance` with `wallet-available` as `.stat-hero`, `.card`, `.field`/`.input`/`.input-affix`, `.banner-danger|warning|success`, `.list-row`, `.badge-*`, `.empty`, `.skeleton`, `.grid-2`/`.grid-wallet`). Snippets for each are in the file.
3. Keep testid elements' text exactly the value (no extra child spans inside `wallet-*`, `*-amount-*`, `current-handle`).
4. Tell me the localStorage key (or cookie name) holding the session token so I can run layout_check signed in, and ping me on each UI commit.
