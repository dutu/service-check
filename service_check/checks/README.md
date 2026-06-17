# Checks

Check modules live in one directory per check:

```text
service_check/checks/<check_name>/
```

Each check directory should include:

- `check.py`: implementation with `run(...)` and `CHECK_METADATA`
- `README.md`: check-specific config, behavior, status, and template fields
- `<order>-<check-name>.example.ini`: minimal config section whose filename
  marks it as an example and suggests drop-in order, for example
  `30-monerod.example.ini`

Checks should document their possible `problem_code` values. Config authors can
override failure messages per code:

```ini
failure_message=Service problem: {message}
failure_message.<problem_code>=Code-specific message using {details}
```

Use the CLI to inspect installed check metadata without maintaining a manual
index in top-level docs:

```bash
service-check --describe-check all
service-check --describe-check <check_name>
```
