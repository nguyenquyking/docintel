# Security: secrets, identities, documents

This repository is public. Nothing that identifies or unlocks private infrastructure may
enter it — not keys, not hosts, not endpoint names, not customer documents.

## Secrets never enter git

* **Configuration comes from the environment.** `backend/docintel/settings.py` reads
  `DOCINTEL_*` variables or a local `.env`; secrets are `SecretStr`, so they are absent
  from `repr()`, logs and the `/api/settings` payload (tested in
  `tests/test_llm_client.py::test_settings_never_expose_secrets` and
  `tests/test_api.py::test_settings_endpoint_hides_secrets`).
* **`.gitignore`** excludes `.env*` (except `.env.example`, placeholders only), key
  material, `.databrickscfg`, databases and the whole `data/` directory (uploaded
  documents, caches, models).
* **`scripts/secret_scan.py`** (stdlib only) blocks provider tokens (Databricks, OpenAI,
  Anthropic, GitHub, Google, AWS, Slack, Hugging Face, Telegram), private keys, JWTs,
  cloud workspace hosts, private IPs, credential-looking assignments with high entropy,
  and forbidden file names.
* **A local denylist** (`.secret-denylist`, git-ignored, one literal or `re:` pattern per
  line) blocks identifiers specific to your environment — company names, internal hosts,
  gateway route names, e-mail addresses — without the repository ever containing them.
* **Hooks** (`sh scripts/install-git-hooks.sh`): `pre-commit` scans staged content,
  `pre-push` scans every object and commit message in history.
* **CI** runs the same scanner over the full history plus gitleaks.

If a secret is ever committed, rotating it is the fix; rewriting history only limits
further exposure.

## Identities for the model endpoint

Prefer, in order:

1. **A service principal per deployment** (OAuth M2M) — for containers. Revocable alone,
   attributable in audit logs, quota and cost booked to the right owner.
2. **A personal CLI login** (`databricks auth login --profile <name>`, then
   `DOCINTEL_DATABRICKS_PROFILE=<name>`) — for development. Calls run under the
   developer's own identity; no shared secret sits on a laptop.
3. A personal access token — only where neither is possible.

Never reuse another system's credentials or endpoint because "it works": the failure is
invisible (tests pass, the bot answers) while cost, quota and audit trail land on someone
else.

## Documents

* Uploaded files live in the deployment's `data/` directory, content-addressed; the API
  binds to `127.0.0.1` by default and has no authentication — put it behind your SSO
  proxy before exposing it.
* Pages sent to a model provider leave the deployment: use a provider agreement with zero
  data retention, or a self-hosted model (any OpenAI-compatible server works).
* Golden sets contain customer data only through the review flow, inside the same
  database; nothing is exported.
