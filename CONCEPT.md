# CST Agent Sandbox (Working Title)

## Purpose

A library that combines **Pydantree** (CST node extraction) with **Cairn** (sandboxed agent orchestration) to automatically analyze and enhance Python code at the node level.

Each CST node (file, class, function) gets its own isolated agent sandbox that can:
- Run linting and static analysis
- Generate unit tests
- Create docstrings and documentation
- Generate sample/fixture data
- Perform other code quality operations

## Core Concept

```
Python Source Code
    ↓
[Pydantree] Extract CST nodes via Tree-sitter queries
    ↓
[Coordinator Agent] Spawn specialized agents for each node
    ↓
[Specialized Agents] Each runs in isolated Cairn sandbox
    ├── Linting Agent
    ├── Test Generator Agent
    ├── Docstring Agent
    └── Sample Data Agent
    ↓
[Cairn Overlays] Each agent writes to its own workspace
    ↓
[Human Review] User accepts/rejects/merges changes
```

## Architecture

### Three-Layer Design

```
┌─────────────────────────────────────────────────────────┐
│  Application Layer                                       │
│  - CLI interface                                         │
│  - Configuration management                              │
│  - Reactive file watching                                │
│  - On-demand triggering                                  │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  Orchestration Layer                                     │
│  - Node discovery (Pydantree queries)                    │
│  - Coordinator agent (meta-agent pattern)                │
│  - Task routing to specialized agents                    │
│  - Context provision (minimal for MVP)                   │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  Execution Layer (Cairn)                                 │
│  - Specialized agent definitions (.pym scripts)          │
│  - Sandboxed execution                                   │
│  - Copy-on-write workspaces                              │
│  - Result collection and merging                         │
└─────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Node Discovery Engine
- Uses Pydantree with custom `.scm` Tree-sitter queries
- Identifies files, classes, and functions
- Extracts node metadata (name, location, text)
- Runtime query evaluation (no pre-generation needed)

**Queries:**
- `function_def.scm` - All function definitions
- `class_def.scm` - All class definitions
- `file.scm` - Python file-level structure

### 2. Coordinator Agent
- Meta-agent that manages specialized agents
- Receives CST node from discovery engine
- Determines which operations to run (lint, test, docstring, etc.)
- Spawns specialized agents via Cairn orchestrator
- Collects and aggregates results

**Responsibilities:**
- Task decomposition (one node → many specialized tasks)
- Agent lifecycle management
- Result aggregation
- Error handling and retry logic

### 3. Specialized Agents
Each is a separate `.pym` script with focused responsibility:

**`lint_agent.pym`**
- Runs linters (ruff, pylint, etc.) on node
- Suggests fixes
- Can auto-apply fixes to sandbox workspace

**`test_generator_agent.pym`**
- Generates unit tests for functions/classes
- Creates test file in appropriate location
- Includes imports and fixtures

**`docstring_agent.pym`**
- Generates or improves docstrings
- Follows configured style (Google, NumPy, Sphinx)
- Injects directly into source code

**`sample_data_agent.pym`**
- Generates example data/fixtures
- Creates JSON/YAML fixture files
- Useful for documentation and testing

### 4. Context Provider
Simple interface for providing code context to agents:

```python
class ContextProvider:
    async def get_context(self, node: CSTNode) -> str:
        """For MVP: returns just the node's text (Option A)"""
        return node.text
```

Future: Can be extended to file-level or smart context.

### 5. Result Aggregator
- Collects outputs from all specialized agents
- Organizes by node and operation type
- Provides unified interface for review
- Handles workspace merging via Cairn

## Data Flow

### Node Processing Pipeline

```python
# 1. Discovery
nodes = await discover_nodes("src/", queries=["function_def", "class_def"])

# 2. For each node, spawn coordinator
for node in nodes:
    coordinator_agent = await spawn_coordinator(node)

    # 3. Coordinator spawns specialized agents
    tasks = coordinator_agent.decompose(node, operations=["lint", "docstring", "test"])

    # 4. Each specialized agent runs in Cairn sandbox
    results = await asyncio.gather(*[
        cairn.spawn_agent(task) for task in tasks
    ])

    # 5. Results live in separate Cairn workspaces
    # User reviews and accepts/rejects via Cairn workflow
```

### Workspace Isolation

```
.agentfs/
├── stable.db                          # Original codebase
├── coordinator-{node-id}.db           # Coordinator workspace
└── specialized-{operation}-{node}.db  # Each specialized agent's workspace
    ├── lint-function-calculate.db
    ├── test-function-calculate.db
    └── docstring-function-calculate.db
```

Each agent writes to its own overlay. User can:
- Accept all changes from one agent type (e.g., all linting)
- Accept specific nodes (e.g., just tests for `calculate()`)
- Reject and retry with different config
- Manually merge conflicts

## Usage Examples

### CLI - On-Demand

```bash
# Analyze entire project
cst-agent analyze src/ --operations lint,test,docstring

# Analyze specific file
cst-agent analyze src/utils.py --operations lint

# Analyze and auto-accept linting
cst-agent analyze src/ --operations lint --auto-accept

# Watch mode (reactive)
cst-agent watch src/ --operations lint,docstring
```

### Programmatic API

```python
from cst_agent import CSTAnalyzer, Operations

analyzer = CSTAnalyzer(
    root_dir="src/",
    queries=["function_def", "class_def"],
    operations=[Operations.LINT, Operations.TEST, Operations.DOCSTRING]
)

# Run analysis
results = await analyzer.analyze()

# Review results
for node, agent_results in results.items():
    print(f"{node.name}:")
    for op, result in agent_results.items():
        print(f"  {op}: {result.status}")

# Accept specific changes
await analyzer.accept(node="calculate", operation=Operations.LINT)

# Reject and retry with different config
await analyzer.reject(node="calculate", operation=Operations.TEST)
await analyzer.retry(node="calculate", operation=Operations.TEST,
                     config={"framework": "pytest"})
```

### Configuration File

```yaml
# cst-agent.yaml
root_dirs:
  - src/
  - lib/

queries:
  - function_def
  - class_def

operations:
  lint:
    enabled: true
    auto_accept: true
    tools:
      - ruff
      - pylint

  test:
    enabled: true
    auto_accept: false
    framework: pytest

  docstring:
    enabled: true
    auto_accept: false
    style: google

  sample_data:
    enabled: false

context_scope: node  # MVP: minimal context

cairn:
  max_concurrent_agents: 10
  timeout: 120
```

## Meta-Agent Pattern

The coordinator uses a two-tier architecture:

```
User Request
    ↓
[Coordinator Agent] (spawned by Cairn)
    ├─→ [Lint Agent] (spawned by coordinator)
    ├─→ [Test Agent] (spawned by coordinator)
    ├─→ [Docstring Agent] (spawned by coordinator)
    └─→ [Sample Data Agent] (spawned by coordinator)
    ↓
[Results aggregated by coordinator]
    ↓
[Coordinator submits to Cairn]
```

**coordinator.pym** (simplified):
```python
from grail import Input, external

# Inputs
node_text = Input("node_text")
node_name = Input("node_name")
operations = Input("operations")  # ["lint", "test", "docstring"]

# External functions
@external
async def spawn_agent(agent_type: str, inputs: dict) -> str:
    """Spawn a specialized agent and return agent_id"""

@external
async def wait_for_agent(agent_id: str) -> dict:
    """Wait for agent completion and get results"""

# Coordinator logic
results = {}
agent_ids = []

for op in operations:
    agent_id = await spawn_agent(
        agent_type=f"{op}_agent",
        inputs={"node_text": node_text, "node_name": node_name}
    )
    agent_ids.append((op, agent_id))

# Gather results
for op, agent_id in agent_ids:
    results[op] = await wait_for_agent(agent_id)

# Submit aggregated results
await submit_result(
    summary=f"Processed {node_name} with {len(operations)} operations",
    changed_files=flatten(r["changed_files"] for r in results.values())
)

return results
```

## MVP Scope

### Phase 1: Core Infrastructure
- [x] Understand Cairn architecture
- [x] Understand Pydantree architecture
- [ ] Set up project structure
- [ ] Implement node discovery with Pydantree
- [ ] Create basic coordinator agent
- [ ] Implement one specialized agent (linting)

### Phase 2: Multi-Agent System
- [ ] Implement remaining specialized agents (test, docstring, sample_data)
- [ ] Build result aggregation system
- [ ] Create basic CLI
- [ ] Add configuration file support

### Phase 3: User Experience
- [ ] Add watch mode (reactive)
- [ ] Implement auto-accept logic
- [ ] Build review interface
- [ ] Add logging and progress reporting

### Phase 4: Polish
- [ ] Documentation
- [ ] Examples and tutorials
- [ ] Performance optimization
- [ ] Error handling refinement

## Future Enhancements

### After MVP
- **Smarter Context**: Upgrade to file-level or dependency-aware context
- **Custom Agents**: Allow users to define their own specialized agents
- **Agent Communication**: Let agents share insights (e.g., test agent uses lint results)
- **Incremental Analysis**: Only re-analyze changed nodes
- **Result Caching**: Cache agent results for unchanged nodes
- **Parallel Execution**: Leverage Cairn's concurrency for faster processing
- **IDE Integration**: VS Code extension for inline results
- **Git Integration**: Auto-run on pre-commit hooks
- **LLM Agents**: Specialized agents that use LLMs for complex operations

## Technology Stack

- **Pydantree**: CST node extraction and query runtime
- **Cairn**: Agent orchestration and sandbox execution
- **Tree-sitter**: Parsing and query matching
- **Pydantic**: Configuration and data validation
- **Typer**: CLI framework
- **Rich**: Terminal output formatting
- **AsyncIO**: Concurrent agent execution

## Open Questions

1. **Naming**: What should we call this library?
   - `cst-agent`? `pycairn`? `node-sandbox`? `code-sage`?

2. **Package Structure**: Single package or monorepo?
   - Single: `pip install cst-agent` (includes coordinator + specialized agents)
   - Monorepo: Core + plugins for each agent type

3. **Agent Distribution**: How are specialized agents deployed?
   - Bundled `.pym` files in package?
   - Registry (like Cairn's registry provider)?
   - User-configurable paths?

4. **Result Format**: How should results be presented?
   - JSON for programmatic use?
   - Rich terminal UI for interactive use?
   - HTML reports?

5. **Testing Strategy**: How do we test the meta-agent pattern?
   - Mock Cairn orchestrator?
   - Integration tests with real Cairn?
   - Separate unit tests for each layer?

## Success Metrics

For MVP to be successful, we should be able to:

✅ Point at a Python file and automatically:
  - Lint it and get actionable suggestions
  - Generate basic unit tests
  - Add/improve docstrings

✅ Review results in isolated workspaces before merging

✅ Accept/reject changes per operation or per node

✅ Run in both on-demand and watch modes

✅ Handle multiple files/nodes concurrently

✅ Provide clear, actionable feedback to users

---

## Next Steps

1. Decide on library name
2. Set up project structure (pyproject.toml, src/, tests/)
3. Implement Pydantree integration (node discovery)
4. Create first specialized agent (linting) as proof of concept
5. Build coordinator agent skeleton
6. Wire everything together with Cairn
7. Create basic CLI and test end-to-end

---

**Questions? Feedback? Ready to start building?**
