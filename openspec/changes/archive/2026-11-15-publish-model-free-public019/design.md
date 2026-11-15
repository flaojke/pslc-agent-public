# Design

## Context
The public repository has its own maintained source history and Apache-2.0 notices. Version 0.19.0 uses one model-free install ZIP plus a prepared source ZIP. Source checkout, prepared SourceZIP and install ZIP have different contents.

## Goals / Non-Goals
Publish reproducible, reviewed source and usable public downloads without moving any existing repository's visibility or history. Do not access working application state, bundle model weights, execute the native LLM, enroll devices or change OS security.

## Decisions
- Base the update on the public repository's own main commit. Transfer only reviewed source/doc/spec deltas; preserve legal files and omit previous private delivery records.
- Generate help from docs/product-guides.json and use public-repository download URLs. Keep release tags immutable; final delivery records follow in a later documentation commit.
- Build fresh Lite and Source ZIPs after public documentation changes. Reuse only individually verified public runtime/native bytes; Source excludes runtime/native binaries and both variants exclude weights and working state.
- Verify file lists, manifests, hashes, licenses and scope before source push. Use a draft release, compare every uploaded asset digest and size, then publish and verify anonymous download access.
- Run isolated regression, source-web build, clean install/Resume and fresh API/browser checks. Keep fixture/packaging evidence separate from real LLM, service and corporate acceptance.

## Trust / Retention / Recovery
No credentials, local QA records, model cache, database or endpoint identity enter source/assets. Credentials used for authorized delivery stay in process RAM. Existing exact-plan confirmations, independent endpoint opt-ins, RAM-only payload handling and 5/20 limits remain unchanged. New output paths refuse overwrite; no old tags/assets are removed.

## Risks / Trade-offs
[Windows policy blocks local runtime] → retain diagnostics and OS protections; downloaded weights do not prove inference.
[Private links or stale delivery claims] → regenerate public-specific URLs and record only actual public readback.
[Remote main changes during work] → compare the expected public parent before fast-forward push; never force.
[Tests use shared locked dependencies] → disclose dependency reuse and do not claim a fresh Node dependency installation.

## Migration Plan
Prepare source and planning records, validate contracts/help/tests, build and verify public ZIPs, run isolated acceptance, push reviewed source/tag without force, upload/verify/publish assets, then record final delivery and archive this change. Working installations and other repositories are not changed by this publication workflow.
