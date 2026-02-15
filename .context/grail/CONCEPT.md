# CONCEPT.md: The Vision for `Grail`

## Overview

**`Grail`** is a high-level Python wrapper designed to make **Monty**—a minimal, secure Python interpreter written in Rust—feel like a native part of the Pydantic ecosystem.

Monty is uniquely designed for running code written by AI agents. However, manually managing raw strings, dictionaries, and type stubs can be cumbersome. Grail automates those tasks using Pydantic models as the source of truth for the sandbox environment.

## Core Philosophy

1. **Schema as Contract:** Use Pydantic models to define the data “shape” of the sandbox. If it’s in the model, it’s available and type-checked in the sandbox.
2. **Safety by Default:** Leverage Monty’s ability to block filesystem and network access while providing a Pythonic interface to grant specific, controlled permissions.
3. **Developer Ergonomics:** Moving from a standard Python function to a secure, isolated Monty execution should require as little as a single decorator.
4. **AOT (Ahead-of-Time) Readiness:** Automate the generation of Monty’s `type_check_stubs` so the internal `ty` type-checker can validate AI-generated code before a single line is executed.
