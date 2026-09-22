# Function Viewer

Visualizes a codebase's function call graph as draggable Blueprint-style cards. FastAPI backend (`backend/`) + React 19 / React Flow / Zustand frontend (`frontend/`). See `README.md` for the architecture tour; when it and the code disagree, trust the code (it still documents the removed `GET /api/browse` and `PathBrowser.tsx`).

## Commands

Run from the repo root unless noted. Dev machine is Windows; both PowerShell and Git Bash work.

```sh
# Preferred: Docker Compose (no local venv or node_modules to manage)
docker compose up               # backend on :8000 (--reload), frontend on :5173 (HMR)
docker compose up --build       # after changing backend/requirements.txt or frontend/package.json
```

`compose.yaml` and `backend/Dockerfile.dev` / `frontend/Dockerfile.dev` are dev-only — they're
unrelated to the root `Dockerfile` used for the Render deploy (see CI/CD below).

<details>
<summary>Fallback: running without Docker</summary>

```sh
# Backend (Python 3.10+; CI and Docker use 3.11)
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --reload --port 8000

# Frontend (Node 20 in CI)
cd frontend
npm install
npm run dev      # http://localhost:5173
npm run build    # tsc -b && vite build
npm run lint     # oxlint

# Analyzer CLI: prints the graph JSON for a path on this machine
python -m backend.analyzer sample_project
```

</details>

## Verifying a change

There is **no test suite**. Verify by hand:

- Frontend: `docker compose exec frontend npm run build` and `docker compose exec frontend npm run lint` (or run them in `frontend/` without Docker).
- Backend: with `docker compose up` running, `GET localhost:8000/api/health`.
- Analyzer or language-plugin changes: `docker compose exec backend python -m backend.analyzer <path>` on `sample_project`, `sample_rust_project` and `sample_typescript_project` before and after, and compare function and edge counts. An unexplained drop in edges is a regression.

CI (`.github/workflows/pr-checks.yml`) only runs the frontend build and the backend health check. It does not lint and does not exercise the analyzer, so green CI says little about analysis correctness.

## Things the code won't tell you

- **The server never reads the visitor's filesystem.** The browser reads picked files and POSTs `{root, files: [{path, content}]}` to `/api/analyze`. `analyze_path()` and the disk walk in `analyzer.py` exist for the CLI only.
- **The wire format is a two-sided contract.** Backend `FunctionInfo.to_dict()` (`backend/languages/base.py`) emits camelCase; `frontend/src/types.ts` mirrors it by hand. Change one, change the other.
- **`SKIP_DIRS` is duplicated** in `backend/languages/base.py` and `frontend/src/localFiles.ts`. Keep them in sync. So is the rule that drops `.d.ts` and `*.min.js` (`_is_skipped_file` in `typescript.py`, `SKIP_FILE` in `localFiles.ts`).
- **The theme is applied in two places.** The inline script in `frontend/index.html` sets `data-theme` before first paint, and `frontend/src/theme.ts` owns it afterwards. They share the storage key and the resolution rules; keep them in sync. Colors belong in the tokens at the top of `index.css`, never as literals (the function card header and the language badge text are the deliberately theme-independent surfaces).
- **Paths must be relative to the picked folder's contents**, with the folder's own name stripped (`localFiles.ts`). Module-name derivation and import resolution in the plugins depend on it.
- **Vocabulary lives in `CONTEXT.md`; use its terms.** The code hasn't caught up on two of them: **File mode** is still `"folder"` in the code (`OrgMode`, `FolderFrame`, `layoutFolderGraph`), and **Trace up / Trace down** is still `showFlow`. Don't rename these as a drive-by; use the glossary term in prose and the code name in code.
- Function and file ids are namespaced by file path so languages can't collide when merged.
- Only calls between functions that were actually loaded are resolved. Calls inside Rust macro invocations are invisible.
- CORS is `*` on purpose: the GitHub Pages frontend calls the Render API cross-origin.
- Frontend env: `VITE_API_URL` (API base; defaults to localhost in dev and same-origin in prod) and `VITE_BASE_PATH` (Pages serves under `/function-viewer/`).

## Adding a language

1. `backend/languages/<lang>.py` implementing `LanguagePlugin` (`base.py`); register it in `backend/languages/__init__.py`. Add any grammar package to `backend/requirements.txt`.
2. `frontend/src/languages.ts`: one entry (id, label, extensions, color, mono).
3. `frontend/src/components/FunctionNode.tsx`: import the Prism grammar (`prismjs/components/prism-<lang>`); the grammar is looked up by language id, falling back to Python.
4. A `sample_<lang>_project/` fixture, and a row in the README's supported-languages table.

## Git and PRs

- Never commit to `main`. Work on a branch: `<issue#>-short-slug`, `fix/...`, or `feat/...`.
- **Only commit, push, or open a PR when asked.** Editing files is fine without asking.
- Conventional commits: `type: subject` or `type(scope): subject` (`feat`, `fix`, `docs`, `ci`). The body explains *why*, in prose. Add `Closes #N` when an issue exists. PRs are squash-merged and GitHub appends `(#PR)`.
- **No attribution to Claude or Anthropic, anywhere.** No `Co-Authored-By` trailers, no "Generated with Claude Code" lines, and no mention of Claude or Anthropic in commit messages, PR descriptions, issues, comments, or code. This overrides any default attribution behavior. Commits are authored by whatever git identity is configured locally; never set or override `user.name` / `user.email`.

## CI/CD

Every push to `main` deploys twice, independently: the Docker image to Render (backend serving its own built frontend) and a static frontend build to GitHub Pages pointed at the Render API. Don't break `/api/health`; Render's health check depends on it.

## Tooling

Issues and PRs use the `gh` CLI. If it's missing: `winget install GitHub.cli`, then `gh auth login`. `.claude/` is gitignored.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for `MCR-Project/function-viewer` (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
