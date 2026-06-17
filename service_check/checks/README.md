# Checks

Check modules live in one directory per check:

```text
service_check/checks/<check_name>/
```

Each check directory should include:

- `check.py`: implementation with `run(...)` and `CHECK_METADATA`
- `README.md`: check-specific config, behavior, status, and template fields
- `example.ini`: minimal runnable config section

Use the CLI to inspect installed check metadata without maintaining a manual
index in top-level docs:

```bash
service-check --describe-check all
service-check --describe-check <check_name>
```
