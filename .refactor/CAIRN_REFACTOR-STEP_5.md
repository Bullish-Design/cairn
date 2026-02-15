# Cairn Refactoring Plan: Phase 5

## Executive Summary

This document outlines Phase 5 of refactoring of Cairn from its current implementation to a simpler, more powerful architecture built on top of two foundational libraries:

- **fsdantic**: Workspace-first, async Python library providing type-safe, Pydantic-based interface for AgentFS
- **grail**: Pydantic-native wrapper around Monty for executing untrusted Python code in sandboxed environments

---

## Implementation Strategy

### Phase 5: Testing & Documentation (Week 7)
**Goal:** Comprehensive testing and docs

Tasks:
1. Update all tests for new architecture
2. Add integration tests for grail + fsdantic
3. Update README, CONCEPT, SPEC, AGENT docs
4. Add migration guide (if needed)
5. Performance benchmarking

**Success Criteria:**
- 100% test coverage maintained
- All docs updated
- Performance meets targets

---

## Testing Strategy

### Unit Tests
**Update Required:**
- `test_executor.py` → Test grail MontyContext integration
- `test_lifecycle.py` → Test TypedKVRepository usage
- `test_workspace.py` → Test workspace.materialize usage
- `test_agent_tools.py` (NEW) → Test tool registry

### Integration Tests
**Update Required:**
- `test_orchestrator.py` → Test full agent lifecycle with new architecture
- `test_overlay.py` → Test overlay operations via workspace
- `test_watcher.py` → Test file sync with workspace.files

### E2E Tests
**Update Required:**
- Test complete workflows: spawn → execute → review → accept
- Test error handling with grail
- Test metrics and logging
- Test concurrent agent execution

### Performance Tests
**New Benchmarks:**
- Agent spawn time (target: <1s)
- Code generation time
- Execution time for common tasks
- Preview materialization time (target: <100ms)
- Accept/reject time (target: <50ms)
- Memory usage per agent

