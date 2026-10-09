# inferra-ai/sync-env

Sends a GitHub Environment's **variables and selected secrets** to the Inferra space
of the same name. Use it in the workflow of a repo that is an Inferra org
(see the Inferra docs: GitHub-native orgs).

```yaml
# .github/workflows/inferra-env.yml
name: inferra-env
on:
  push:
    branches: [main]
  workflow_dispatch: {}
permissions: {}
jobs:
  sync:
    permissions:
      id-token: write
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        space: [prod, stage, preview]   # Use the spaces from .inferra/org.yaml.
    environment: ${{ matrix.space }}
    steps:
      - uses: inferra-ai/sync-env@8f6330a7af3dd02ffb6c103081d43050804b3b38 # v1.0.2
        with:
          space: ${{ matrix.space }}
          variables: ${{ toJSON(vars) }}
          secrets: |
            {
              "DATABASE_URL": ${{ toJSON(secrets.DATABASE_URL) }},
              "PAYMENTS_API_KEY": ${{ toJSON(secrets.PAYMENTS_API_KEY) }}
            }
          # api-url: https://api.dev.inferralab.com   # for a non-production Inferra
```

Only listed secrets leave GitHub. Use `toJSON` for each selected value so
quotes, newlines and backslashes remain valid JSON. A listed secret that isn't
set in one Environment (say `STRIPE_KEY` only in `prod`) is skipped there with
a warning naming it. For no selected secrets, use `secrets: '{}'`. Variables are also imported by the Inferra GitHub
App without this workflow.

How it works:

- No Inferra token is stored in GitHub. The job asks GitHub for an **OIDC
  token** (audience `inferra`) naming this repository and environment;
  Inferra verifies it with GitHub's published keys and only accepts it for
  the org this repo is, and the space of the same name.
- The space's GitHub-managed variables and secrets are **replaced** by what is
  sent. Remove a name from the mapping to delete its synced value in Inferra on
  the next successful run. An empty or omitted mapping clears all synced
  secrets. Deleting a selected secret in GitHub removes it from Inferra on the
  next run, the same as removing it from the mapping.
  Secrets are stored encrypted and never shown back; apps in the space
  restart when a value changed.
- GitHub's own `github_token` is never sent.
- Secret values are never printed; validation errors list names only.

## Protect production

Inferra accepts a sync from **any job GitHub lets run in the Environment**:
it checks the repository, the Environment and that the token is fresh and
unused, not the branch or workflow. So protect production Environments in
GitHub (Settings → Environments → prod):

- **Deployment branches and tags**: only `main` (or your release branch).
- **Required reviewers**, if prod values should need an approval.

Without these, a workflow on any branch that declares `environment: prod`
can replace prod's values.

## Limits and retries

- At most 100 variables and secrets in total, 512 KB together, 32 KB each.
- An older run can't overwrite a newer one (a delayed job is refused).
- If some apps couldn't be restarted, the values are still saved and the
  step fails with a message saying so; Inferra retries those apps, and
  re-running the workflow restarts any still behind.

## Tests

Run `python3 -m unittest discover -s tests -v`. Tests require Bash and jq and
mock curl locally; they make no network requests.
