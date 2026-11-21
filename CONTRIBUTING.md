# Contributing

- Python 3.11+. `pip install -e ".[dev]"`, then `ruff check .`, `ruff format --check .` and `pytest` before opening a pull request.
- Changing a tier's requirements: update `REQUIREMENTS` in `policy.py`, the tests, and the table in `aurai-docs`.
- Changing a suite: its hash changes, so earlier evidence stops counting. Update `evals/replay/<suite>.yaml`, run `aurai-governance evals check`, and run the suite live again. Changing `evals/thresholds.yaml` needs the owner's review.
- Changing trace attributes: update `tracing.py`, `docs/tracing-conventions.md` and the tests together; other repos depend on the names.
- Charts: keep Helm templates free of secrets; credentials come from Kubernetes Secrets. Run `helm lint` and `helm template` when you have Helm available.
- No real names, customer data or credentials. Examples use fictional teams and synthetic values.
- Use clear, imperative commit messages.
