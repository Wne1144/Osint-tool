# Build and acceptance status (2026-10-26)

## Verified in this workspace

- Python syntax compile: passed on CPython 3.13.5.
- Automated tests: 7/7 passed on the Linux x86_64 sandbox (see `tests/test_app.py`).
- Live adapter smoke test: Wikipedia MediaWiki API returned five results for the test query `Ada Lovelace`; an end-to-end temporary-database run completed with actual API responses and produced eight source-result records across two iterations. This demonstrates the adapter worked from this sandbox at that moment only; external availability is not guaranteed.
- SQLite schema initializes; local API token gate and project cascade deletion tested.
- Chromium browser smoke test on the local sandbox: UI loaded and a synthetic empty project could be created; no external search was launched. This is not a target-platform UI test.

## Not verified / acceptance criteria not met

- No Windows 10/11 clean-machine build or test.
- No Raspberry Pi OS ARM64/ARMv7 build or test.
- No self-contained executable/runtime in the ZIPs; system Python is required.
- No measured two-minute startup result. Startup timing criterion is unproven.
- No secure-system/code audit or legal review.
- No image handling or face-search provider.
- No general web search, social network, email-specific enrichment, full-page extraction, or independently corroborated source adapter.
- JSON backup restore is covered by a basic round-trip endpoint test; the file picker/UI workflow is not browser-tested.
- No proof of accurate identity resolution; this application deliberately does not claim or automate it.

Accordingly, files named `*-source.zip` are source starters, not binary releases or verified platform releases. `armv7-source.zip` is only architecture-independent Python source; it is not an assertion of ARMv7 runtime compatibility.
