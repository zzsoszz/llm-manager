# Contributing

Use Python 3.10+ and keep runtime dependencies at zero unless a capability cannot reasonably be implemented with the standard library.

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```

Changes should include tests for externally observable behavior. Monitoring adapters must distinguish measured values, estimates, stale samples, and unavailable data. Read-only commands must not start model servers, submit generation requests, or cancel inference. New GPU probes must remain optional.

Keep machine-specific paths, credentials, chat databases, model weights and runtime logs out of commits. Public examples should use configurable paths and environment-variable references.
