#!/bin/sh
# Enable the versioned hooks in .githooks for this clone (run once after cloning).
set -e
cd "$(git rev-parse --show-toplevel)"
chmod +x .githooks/* scripts/secret_scan.py
git config core.hooksPath .githooks
echo "git hooks enabled: $(ls .githooks | tr '\n' ' ')"
[ -f .secret-denylist ] || echo "tip: add internal names/hosts to .secret-denylist (git-ignored) so the scanner blocks them too"
