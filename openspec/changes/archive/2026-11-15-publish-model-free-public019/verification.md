# Verification

| Contract | Check | Result |
|---|---|---|
| DS-001/002 model-free distribution | Actual Lite/Source manifests, file hashes, CRC and scope | PASS; no weights or working state |
| DS-004 owned installation | Fresh install and Resume, 32 pinned dependencies | PASS |
| DS-005 native entrypoints | Focused regression | PASS; no VM operations |
| Product help and source closure | Four generated guides, TypeScript/Vite and independent SourceZIP web build | PASS |
| Fresh API/UI | 13 screens,390px,0 page errors; guarded downloads/runtime/external HTTP | PASS;0 forbidden attempts, all owned processes stopped |
| DS-003 publication | Public source/tag/assets and anonymous download readback | PASS;7 asset digests/sizes and7 anonymous bounded GET206 checks |

67 targeted tests passed. Existing locked Node dependencies and a prepared Python wheelhouse were reused. Real LLM inference/download, corporate providers, deployed services and production scale are not accepted. No local working state was accessed. Local QA records/screens are not published.

Actual release: https://github.com/flaojke/pslc-agent-public/releases/tag/v0.19.0

Tagged source: `31125c54eb4d569612944dd879533a400f25f4d5`; sole parent `a669654b56eefc0850ecdeda384dbba5b0256320`. No force or history import. Actual public checksums and URLs: docs/RELEASE_0.19.0.json. Final documentation follows the immutable tag.
