# Contributing

PRs welcome. This is a small library; non-trivial contributions can
typically be reviewed in a day.

## Local dev setup

```bash
git clone https://github.com/KVMFleet/bmc-adapters
cd bmc-adapters
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running checks

```bash
ruff check .
mypy bmc_adapters
pytest
```

CI runs the same three. PRs must pass all of them.

## What we'll merge

- **Bug fixes** with a reproducing test.
- **New vendor support** with the test-fixture pattern in
  [supported-vendors.md](supported-vendors.md).
- **Improved error messages** when a BMC quirk leads to a confusing
  `RedfishError`.
- **Documentation improvements**, including this doc.

## What we'll push back on

- **Feature additions outside the four core operations** (heartbeat,
  power, virtual media, session lifecycle). If you want to add a new
  capability, open an issue first to discuss whether it belongs in
  this library or in a downstream adapter on top.
- **Vendor-specific code paths in the client.** The library absorbs
  vendor quirks via generic logic (auth-mode detection, MediaTypes
  inspection, etc.). Adding `if vendor == "iDRAC":` branches is the
  start of unmaintainable code.
- **Synchronous APIs.** The library is async-only. If you need a
  sync wrapper, add it as a separate module.

## Coding conventions

- `ruff` defaults (line length 100).
- `mypy --strict`.
- Type hints on every public function and dataclass field.
- Comment the *why*, not the *what*. If a workaround exists, name the
  vendor + firmware version it's working around.
- Tests live in `tests/`. Use `respx` to mock httpx; no live-BMC tests
  in CI.

## Release process

Maintainers only:

1. Bump version in `pyproject.toml` and `bmc_adapters/__init__.py`.
2. Update `CHANGELOG.md`.
3. Tag the commit `v<version>`.
4. CI publishes to PyPI on tag push.

## License

By contributing, you agree your contribution is licensed under the
Apache 2.0 License (the project's license).
