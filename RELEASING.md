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

### npm — NPM_TOKEN secret + signed provenance

The npm package is unscoped `loupe-ai` (same name as PyPI), so there's
no org to create. Auth uses an automation token stored as a GitHub
secret — the method that reliably publishes a brand-new package on the
first try. `--provenance` still attaches a signed build attestation.

1. Log in at https://www.npmjs.com (the account that will own `loupe-ai`).
2. **Account → Access Tokens → Generate New Token → Granular Access
   Token** (or "Automation"):
   - Expiration: choose (or "no expiration" for a set-and-forget token).
   - Packages and scopes: **Read and write**.
   - (Granular only) add `loupe-ai` once it exists, or allow all to
     cover the first publish.
   - Copy the token (starts with `npm_…`) — shown once.
3. In the GitHub repo: **Settings → Secrets and variables → Actions →
   New repository secret**:
   - Name: `NPM_TOKEN`
   - Value: the `npm_…` token
4. **Settings → Environments → New environment → `npm`** (optional
   reviewer gate).

That's it. The next `git tag vX.Y.Z` publishes `loupe-ai` to npm with
provenance. The publish step is idempotent — re-running an
already-published version is a no-op, never an error.

## Pre-publish sanity (optional, local)

```bash
# Python — build + validate the artifacts
cd packages/loupe-py && python -m build && python -m twine check dist/*

# npm — see exactly what would ship
cd packages/loupe-ts && npm run build && npm pack --dry-run
```
