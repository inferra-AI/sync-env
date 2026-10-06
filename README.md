# inferra-ai/sync-env

Sends a GitHub Environment's **variables and secrets** to the Inferra space
of the same name. Use it in the workflow of a repo that is an Inferra org
(see the Inferra docs: GitHub-native orgs).

```yaml
# .github/workflows/inferra.yml
name: inferra-env
on:
  push:
    branches: [main]
  workflow_dispatch: {}
permissions:
  id-token: write          # the only permission it needs: the Action proves who it is with GitHub's OIDC token
jobs:
  sync:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        environment: [prod, stage, preview]   # GitHub Environment name = Inferra space name
    environment: ${{ matrix.environment }}
    steps:
      - uses: inferra-ai/sync-env@v1
        with:
          space: ${{ matrix.environment }}
          variables: ${{ toJSON(vars) }}
          secrets: ${{ toJSON(secrets) }}
          # api-url: https://api.dev.inferralab.com   # for a non-production Inferra
```

How it works:

- No Inferra token is stored in GitHub. The job asks GitHub for an **OIDC
  token** (audience `inferra`) naming this repository and environment;
  Inferra verifies it with GitHub's published keys and only accepts it for
  the org this repo is, and the space of the same name.
- The space's variables and secrets are **replaced** by what is sent:
  something deleted in GitHub disappears in Inferra on the next sync.
  Secrets are stored encrypted and never shown back; apps in the space
  restart when a value changed.
- GitHub's own `github_token` is never sent.
- Nothing is printed except counts.

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
