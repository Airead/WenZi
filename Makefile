.PHONY: dev run-lite docs docs-serve lint test build build-fast build-lite build-lite-fast build-dmg build-lite-dmg clean sync-registry

# Overridable environment variables for development:
# WENZI_CONFIG_DIR    — config directory path (default: ~/.config/WenZi)
# WENZI_VERSION       — build variant: "lite" or "standard"
# WENZI_APP_PATH      — override app bundle path (updater testing)
# WENZI_DEV_VERSION   — override version string (update-check testing)
# WENZI_FORCE_AUTO_UPDATE — set to "1" to enable auto-update in dev mode

# Run the app in development mode (Standard — all backends)
# Usage: WENZI_CONFIG_DIR=/tmp/wenzi-test make dev
dev:
	uv sync --all-extras
	uv run python -m wenzi

# Run Lite version (Apple Speech + Remote API only)
# Usage: WENZI_CONFIG_DIR=/tmp/wenzi-test make run-lite
run-lite:
	test -d .venv-lite || uv venv .venv-lite
	UV_PROJECT_ENVIRONMENT=.venv-lite uv sync
	WENZI_VERSION=lite UV_PROJECT_ENVIRONMENT=.venv-lite uv run python -m wenzi

# Build HTML documentation from docs/*.md
docs:
	uv run --with markdown python scripts/build_docs.py

# Serve the site locally
docs-serve: docs
	@echo "Serving at http://localhost:8003"
	python3 -m http.server 8003 -d site

# Lint with ruff
lint:
	uv run ruff check

# Run tests with coverage
test:
	uv run pytest tests/ -v --cov=wenzi

# Build the .app bundle (Standard)
build:
	./scripts/build.sh

# Build the .app bundle (Standard, incremental — no clean)
build-fast:
	./scripts/build.sh --fast

# Build the Lite .app bundle
build-lite:
	./scripts/build-lite.sh

# Build the Lite .app bundle (incremental — no clean)
build-lite-fast:
	./scripts/build-lite.sh --fast

# Package .app into .dmg (run after build/build-lite)
build-dmg:
	./scripts/build-dmg.sh

build-lite-dmg:
	./scripts/build-dmg.sh --lite

# Remove build artifacts
clean:
	rm -rf build/ dist/

# Regenerate plugins/registry.toml from plugins/*/plugin.toml
sync-registry:
	uv run python scripts/sync_registry.py
