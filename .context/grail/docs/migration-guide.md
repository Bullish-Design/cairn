# Migration Guide

## Direct Monty → Grail

Before:
- manually serialize inputs and parse outputs
- manually wire tools/type stubs

After:
- define models and use `MontyContext`
- optionally use `execute_with_resilience()` for retries/fallbacks
