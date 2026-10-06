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
  contents: read
  id-token: write          # required: the Action proves who it is with GitHub's OIDC token
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
