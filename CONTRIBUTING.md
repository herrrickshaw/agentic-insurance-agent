# Contributing

1. Pick a `ready` story from `docs/product/BACKLOG.yaml` (highest WSJF first). Open a branch named `<story-id>-short-name`.
2. **Test first.** Write a failing test tagged `@pytest.mark.story("ID")` (or a Gherkin scenario tagged `@ID`), see it fail for the right reason, then implement.
3. Run `make ci`. Use `make mutation` when you touch core logic.
4. Commit with [Conventional Commits](https://www.conventionalcommits.org) (`feat:`, `fix:`, `test:`, `docs:`, `chore:`); one logical change per commit.
5. Open a PR using the template. Keep it small; merge to `main` quickly (trunk-based).
6. Never commit secrets, real customer data, or the generated `.mp4` files.

Setup: `make setup`; pre-commit hooks: `pre-commit install && pre-commit install --hook-type pre-push`.
