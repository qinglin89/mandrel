# Architecture

High-level system structure.

Components:

- strategy runtime
- execution engine
- risk engine
- exchange connectors

Data flow:

strategy
↓
execution
↓
connector
↓
exchange

Design goals:

- deterministic execution
- event-driven architecture
