# Contributing Guide

## 1. Branching

- Create feature branches from your main integration branch.
- Use descriptive branch names, for example:
  - `feat/forecast-bbox-ui`
  - `fix/auth-header-validation`

## 2. Local development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[backend,test]
cp .env.backend.example .env.backend
python -m aqi_pipeline.backend --reload
```

### Frontend

```bash
cd frontend
cp .env.example .env
npm ci
npm run dev
```

## 3. Coding expectations

- Keep changes scoped and focused.
- Avoid committing secrets (`.env`, API keys, tokens).
- Prefer clear names and small functions over complex blocks.
- Update docs when behavior, API contracts, or setup changes.

## 4. Validation before PR

### Backend checks

```bash
cd backend
source .venv/bin/activate
pytest
```

### Frontend checks

```bash
cd frontend
npm run lint
npm run build
```

## 5. Pull request checklist

- [ ] Problem statement is clear
- [ ] Setup or env changes documented
- [ ] Tests added/updated (if applicable)
- [ ] No secrets in commits
- [ ] README/docs updated where needed

## 6. Reporting issues

Include:
- Expected behavior
- Actual behavior
- Steps to reproduce
- Logs/error messages
- Environment details (OS, Python/Node versions)
