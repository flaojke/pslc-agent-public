# PSLC Agent development

Keep runtime changes separate from documentation and packaging changes. Read relevant accepted specifications in openspec/specs before behavioral changes. Use the project-local OpenSpec tooling for new proposal, design and task records. Validate affected specifications and tests before delivery.

Never persist raw endpoint logs, full command output or model context. Fail closed on identity, policy and confirmation errors. Mutations require exact-plan confirmation; reconcile uncertain outcomes before retrying. Do not test host-changing actions or enroll real targets without explicit authorization.

Product help source is docs/product-guides.json. After changing help text, run python scripts/generate_product_guides.py and its --check mode. Preserve accounts, device identities, model secrets and local opt-ins. The public bootstrap admin/admin remains unchanged; documentation must warn to change it before network access.

Run python -m pytest and npm run build in an isolated environment. Do not claim external service, native platform or production acceptance from fixture tests. Keep local state, QA evidence, credentials and private project records out of source distributions. Include LICENSE and preserve third-party notices. Never change OS security policy to make a test pass.
