# Cairn Refactoring Plan

This directory contains a comprehensive, step-by-step refactoring plan based on the code review documented in `CODE_REVIEW.md`. Each step is designed to be self-contained and can be assigned to a developer to implement independently.

## Overview

The refactoring work has been organized into **8 logical steps**, each addressing specific issues identified in the code review. The steps are ordered to minimize dependencies and allow for incremental progress.

**Total Estimated Effort:** 50-60 hours (6-7.5 developer days)

---

## Step Summary

### Step 1: Foundation - Error Infrastructure & Constants
**File:** `REVIEW_REFACTOR-STEP_1.md`
**Priority:** 🔴 CRITICAL (Foundation for other steps)
**Estimated Effort:** 4-6 hours

**What it does:**
- Creates comprehensive error hierarchy with base `CairnError` class
- Replaces all magic numbers with named constants
- Adds missing module docstrings

**Why first:**
- Provides foundation for error handling in all later steps
- No dependencies on other refactoring work
- Backward compatible - doesn't break existing code

**Key deliverables:**
- `cairn/exceptions.py` - Complete error hierarchy
- `cairn/constants.py` - All constants defined
- Tests for exceptions and constants

---

### Step 2: Type Safety Improvements
**File:** `REVIEW_REFACTOR-STEP_2.md`
**Priority:** 🟡 HIGH
**Estimated Effort:** 3-4 hours

**What it does:**
- Reduces `Any` usage throughout codebase
- Adds TypedDict for structured data (API responses)
- Adds return type annotations to all functions
- Creates Protocol definitions for interfaces

**Why second:**
- Uses constants from Step 1
- Improves IDE support and catches bugs at type-check time
- Pure additive - no behavior changes

**Key deliverables:**
- `cairn/types.py` - Type definitions and TypedDict classes
- Updated type hints in external_functions.py and orchestrator.py
- Type checking tests

---

### Step 3: Error Handling & Resource Cleanup
**File:** `REVIEW_REFACTOR-STEP_3.md`
**Priority:** 🔴 CRITICAL
**Estimated Effort:** 4-5 hours

**What it does:**
- Adds async context managers for workspace management
- Ensures cleanup in finally blocks
- Improves error message consistency
- Fixes silent failures in watcher

**Why third:**
- Uses error hierarchy from Step 1
- Must be done before retry logic (Step 4)
- Critical for preventing resource leaks

**Key deliverables:**
- `cairn/workspace_manager.py` - Workspace lifecycle management
- `cairn/error_formatting.py` - Consistent error messages
- Guaranteed cleanup for all resources

---

### Step 4: Retry Logic Integration
**File:** `REVIEW_REFACTOR-STEP_4.md`
**Priority:** 🟡 HIGH
**Estimated Effort:** 4-6 hours

**What it does:**
- Integrates existing but unused RetryStrategy module
- Adds retry decorators for common patterns
- Applies retries to lifecycle persistence, workspace operations, provider fetches
- Handles transient failures gracefully

**Why fourth:**
- Uses RecoverableError from Step 1
- Needs proper cleanup from Step 3
- Major reliability improvement

**Key deliverables:**
- `cairn/retry_utils.py` - Retry decorators and utilities
- Retry logic applied to all critical operations
- Retry tests

---

### Step 5: Security Hardening
**File:** `REVIEW_REFACTOR-STEP_5.md`
**Priority:** 🔴 CRITICAL (Security)
**Estimated Effort:** 6-8 hours

**What it does:**
- Implements ReDoS protection for regex operations
- Enforces resource limits (timeout, memory) on agent execution
- Adds secrets detection to prevent accidental exposure
- Addresses security vulnerabilities

**Why fifth:**
- Uses SecurityError and TimeoutError from Step 1
- Can be done independently of concurrency work
- Critical security fixes

**Key deliverables:**
- `cairn/regex_utils.py` - Safe regex with timeout
- `cairn/resource_limits.py` - Resource limit enforcement
- `cairn/secrets_detection.py` - Secrets scanning
- Security tests

---

### Step 6: Concurrency & Performance
**File:** `REVIEW_REFACTOR-STEP_6.md`
**Priority:** 🟡 HIGH
**Estimated Effort:** 6-8 hours

**What it does:**
- Fixes race condition in lifecycle persistence with optimistic locking
- Replaces signal polling with filesystem events (watchfiles)
- Adds LRU cache for workspaces to limit memory usage
- Adds queue size limits and backpressure

**Why sixth:**
- Uses VersionConflictError from Step 1
- Needs retry logic from Step 4
- Performance improvements build on stable foundation

**Key deliverables:**
- `cairn/workspace_cache.py` - LRU workspace cache
- Optimistic locking in lifecycle operations
- Filesystem events for signals (no more polling)
- Concurrency tests

---

### Step 7: Code Structure Refactoring
**File:** `REVIEW_REFACTOR-STEP_7.md`
**Priority:** 🟢 MEDIUM
**Estimated Effort:** 4-5 hours

**What it does:**
- Breaks down large `_run_agent()` method into focused methods
- Extracts helper functions for better organization
- Improves code structure and readability
- Pure refactoring - no behavior changes

**Why seventh:**
- Can be done after all functional improvements
- Benefits from all previous improvements being in place
- Makes testing easier

**Key deliverables:**
- `cairn/orchestrator_helpers.py` - Extracted helpers
- `_run_agent()` method under 30 lines
- Each phase as separate method
- Phase-specific tests

---

### Step 8: Testing Infrastructure
**File:** `REVIEW_REFACTOR-STEP_8.md`
**Priority:** 🔴 CRITICAL
**Estimated Effort:** 10-12 hours (largest step)

**What it does:**
- Adds comprehensive integration tests (end-to-end workflows)
- Adds concurrency tests to catch race conditions
- Adds failure injection tests for retry logic
- Adds CLI tests
- Adds crash recovery tests
- Adds resource exhaustion tests
- Expands performance benchmarks

**Why last:**
- Validates all previous refactoring work
- Requires all other steps to be complete
- Provides confidence in the entire system

**Key deliverables:**
- `tests/integration/` - Complete integration test suite
- CLI tests for all commands
- Concurrency and race condition tests
- 85%+ code coverage

---

## Implementation Strategy

### Recommended Approach

1. **Sequential Implementation**: Implement steps in order (1 → 8) to minimize issues
2. **One Step at a Time**: Complete and test each step before moving to the next
3. **Incremental Commits**: Commit after each step completion
4. **Test After Each Step**: Ensure all tests pass before proceeding

### Parallel Implementation (Advanced)

If multiple developers are available, these steps can be done in parallel:

**Phase 1** (Foundation):
- Step 1 (solo, required for others)

**Phase 2** (Can be parallel after Step 1):
- Step 2 (Type Safety)
- Step 3 (Error Handling)

**Phase 3** (Can be parallel after Phase 2):
- Step 4 (Retry Logic) - needs Step 1, 3
- Step 5 (Security) - needs Step 1
- Step 7 (Structure) - mostly independent

**Phase 4** (Needs Phase 3):
- Step 6 (Concurrency) - needs Steps 1, 4

**Phase 5** (Final):
- Step 8 (Testing) - needs all previous steps

---

## Step Dependencies

```
Step 1 (Foundation)
  ├─> Step 2 (Types)
  ├─> Step 3 (Cleanup) ─> Step 4 (Retry) ─> Step 6 (Concurrency)
  ├─> Step 5 (Security)
  └─> Step 7 (Structure)
                └─> Step 8 (Testing) <─ All steps
```

---

## Progress Tracking

### Checklist

- [ ] Step 1: Foundation - Error Infrastructure & Constants
- [ ] Step 2: Type Safety Improvements
- [ ] Step 3: Error Handling & Resource Cleanup
- [ ] Step 4: Retry Logic Integration
- [ ] Step 5: Security Hardening
- [ ] Step 6: Concurrency & Performance
- [ ] Step 7: Code Structure Refactoring
- [ ] Step 8: Testing Infrastructure

### Metrics to Track

For each step, track:
- [ ] All files created
- [ ] All files modified
- [ ] All tests written
- [ ] All tests passing
- [ ] Code coverage maintained/improved
- [ ] Documentation updated
- [ ] Code review completed

---

## Testing Strategy

### After Each Step

```bash
# Run all tests
pytest

# Check code coverage
pytest --cov=cairn --cov-report=html

# Run type checking
mypy cairn/

# Run linting
ruff check cairn/

# Run formatting check
ruff format --check cairn/
```

### Final Validation (After Step 8)

```bash
# Run all tests including slow ones
pytest --cov=cairn --cov-report=html

# Run integration tests
pytest tests/integration/ -v

# Run performance benchmarks
pytest -m benchmark

# Generate coverage report
coverage report --fail-under=85
```

---

## Documentation Updates

### After Completion

Update the following documentation:
1. **README.md** - Update with new features (retry logic, security features)
2. **SPEC.md** - Document new error hierarchy, resource limits
3. **TESTING.md** - Document test suite organization
4. **MIGRATION.md** - Document any breaking changes (should be minimal)

---

## Common Issues & Solutions

### Issue: Import Cycles
**Solution:** If you encounter circular imports, use TYPE_CHECKING:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.orchestrator import CairnOrchestrator
```

### Issue: Tests Failing After Refactoring
**Solution:** Update test fixtures to use new patterns (context managers, etc.)

### Issue: Mypy Errors
**Solution:** Add type: ignore comments only as last resort, prefer fixing types

### Issue: Performance Regression
**Solution:** Run benchmarks before and after, optimize if needed

---

## Questions?

If you have questions while implementing:

1. **Check the step file** - Each has detailed instructions and examples
2. **Check CODE_REVIEW.md** - Original analysis and recommendations
3. **Check existing code** - Look for similar patterns in the codebase
4. **Ask for clarification** - Better to ask than implement incorrectly

---

## Success Criteria

The refactoring is complete when:

- ✅ All 8 steps implemented and tested
- ✅ All tests passing (including new integration tests)
- ✅ Code coverage ≥ 85%
- ✅ No ruff or mypy errors
- ✅ Performance benchmarks show no regression
- ✅ Documentation updated
- ✅ Code review approved

---

## Estimated Timeline

### Conservative (Single Developer)
- Week 1: Steps 1-3 (Foundation, Types, Cleanup)
- Week 2: Steps 4-5 (Retry, Security)
- Week 3: Steps 6-7 (Concurrency, Structure)
- Week 4: Step 8 (Testing) + Documentation
- **Total: 4 weeks**

### Aggressive (2-3 Developers)
- Week 1: Steps 1-4 (Foundation through Retry)
- Week 2: Steps 5-7 (Security, Concurrency, Structure)
- Week 3: Step 8 (Testing) + Documentation
- **Total: 3 weeks**

---

**Generated:** 2026-02-16
**Based on:** CODE_REVIEW.md (Cairn v0.1.0)
**Total Issues Addressed:** 25+ issues and recommendations

---

*This refactoring plan transforms Cairn from "B+ Very Good" to "A- Production Ready" by addressing all critical and high-priority issues identified in the comprehensive code review.*
