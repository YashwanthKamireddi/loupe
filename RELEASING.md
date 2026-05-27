# Releasing Loupe

Publishing is automated and **secret-free** — no API tokens live in the
repo or in GitHub secrets. Both packages publish via OIDC Trusted
Publishing, triggered by a version tag.

## Cut a release (every time)

```bash
# 1. Bump the version in ALL FOUR sources to the same value:
#      packages/loupe-py/src/loupe/_version.py   __version__ = "X.Y.Z"
#      packages/loupe-py/pyproject.toml          version = "X.Y.Z"
#      packages/loupe-ts/package.json            "version": "X.Y.Z"
#      packages/loupe-ts/src/index.ts            export const VERSION = "X.Y.Z"
#    (tests/test_version_parity.py fails the build if they drift.)

# 2. Commit, then tag + push:
git commit -am "vX.Y.Z — <summary>"
git tag vX.Y.Z
git push origin main --tags
```

The `Release` workflow then: runs the test gate, verifies the tag
matches the package version, builds both packages, and publishes
`loupe` → PyPI and `@loupe/sdk` → npm. A mistagged or test-failing
release is rejected before anything is published.

## One-time setup (do this once, before the first release)

### PyPI — Trusted Publishing

1. Create the project owner account at https://pypi.org.
2. Go to **Publishing** → **Add a pending publisher** and enter:
   - PyPI Project Name: `loupe-ai`
   - Owner: `YashwanthKamireddi`
   - Repository: `loupe`
   - Workflow name: `release.yml`
   - Environment: `pypi`
3. In the GitHub repo: **Settings → Environments → New environment →
   `pypi`** (optionally add a required-reviewer rule for a manual gate).

No token is created or stored — PyPI verifies the GitHub OIDC identity
at publish time.

### npm — Trusted Publishing + provenance

1. Create/own the `@loupe` scope at https://www.npmjs.com.
2. On the package settings (after a first manual publish, or via the
   org's package config), enable **Trusted Publishing** pointing at this
   repo's `release.yml` and the `npm` environment.
3. In the GitHub repo: **Settings → Environments → New environment →
   `npm`**.

If npm Trusted Publishing isn't enabled for the package yet, the first
publish can be done manually:

```bash
cd packages/loupe-ts
npm run build
npm publish --provenance --access public   # requires `npm login` locally
```

After that first publish, enable Trusted Publishing so every subsequent
tagged release is automated and tokenless.

## Pre-publish sanity (optional, local)

```bash
# Python — build + validate the artifacts
cd packages/loupe-py && python -m build && python -m twine check dist/*

# npm — see exactly what would ship
cd packages/loupe-ts && npm run build && npm pack --dry-run
```
