# Deployment — Candidate Tracker API

End-to-end deployment of the containerised API to Azure Container Apps, with a
managed PostgreSQL backend.

Local development uses `docker compose`. Azure uses this document. The two are
deliberately different, and the "Architecture" section explains why.

---

## 1. Architecture

| Concern | Local (compose) | Azure |
| --- | --- | --- |
| API | `api` container, built from `Dockerfile` | Container App, same image from ACR |
| Database | `db` container, `postgres:16-alpine` | Azure Database for PostgreSQL flexible server |
| Image source | built on the host | Azure Container Registry |
| Secrets | `.env`, gitignored | Container Apps secrets |
| Storage | named volume `pgdata` | managed, backed up by Azure |
| Reachability | `localhost:8001` | public FQDN with managed TLS |

### Why not run Postgres as a second container app

The compose file runs Postgres as a container because that is the right answer
for local development: disposable, fast, no cloud dependency.

It is the wrong answer on Container Apps. Container Apps is designed for
stateless workloads — replicas are created and destroyed on scale events,
revisions, and platform maintenance. Without persistent storage attached, a
Postgres container loses its entire data directory on any of those events. The
database would appear to work and then silently empty itself.

Azure Database for PostgreSQL flexible server gives durable storage, automated
backups, patching, and point-in-time restore. On the Burstable B1ms tier it is
the cheapest managed option and is sufficient for this workload.

### Why migrations run at startup

The compose `command` runs `alembic upgrade head` before `uvicorn`. The same
applies on Azure: the container app's startup command runs migrations against
the managed database, so a new revision brings the schema with it.

This is acceptable at one replica. It does not generalise: with several
replicas starting at once, each would attempt the migration concurrently.
Before scaling past one replica, migrations should move to a separate job that
runs once per deploy. Noted as known future work rather than solved here.

---

## 2. Prerequisites

- Azure CLI installed and signed in (`az login`)
- An **enabled** subscription — `az account show` must not report `Disabled`
- Docker running locally (for local verification; the image is built in Azure)
- The Container Apps CLI extension

```bash
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.DBforPostgreSQL
```

Provider registration is a one-off per subscription and can take a few minutes.
Check with `az provider show --namespace Microsoft.App --query registrationState`.

---

## 3. Required code change: SSL to the managed database

Azure Database for PostgreSQL **requires TLS**. The local `postgres:16-alpine`
container does not, so this only fails once deployed — the app starts, the first
query is refused, and the failure looks like a connection error rather than a
configuration one.

`app/config.py` must be able to request SSL:

```python
class Settings(BaseSettings):
    ...
    postgres_sslmode: str = "prefer"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            f"?sslmode={self.postgres_sslmode}"
        )
```

`prefer` is the default so local development and the test suite are unaffected.
Azure sets `POSTGRES_SSLMODE=require`, which fails closed if TLS is unavailable.

---

## 4. Variables

Set these once per shell session. Names must be globally unique where noted.

```powershell
$RESOURCE_GROUP   = "rg-candidate-tracker"
$LOCATION         = "eastus"
$ACR_NAME         = "acrcandidatetracker"        # globally unique, lowercase alphanumeric only
$PG_SERVER        = "pg-candidate-tracker"       # globally unique
$PG_ADMIN         = "trackeradmin"               # cannot be 'postgres', 'admin', 'azure_superuser'
$PG_DATABASE      = "appdb"
$ENVIRONMENT      = "cae-candidate-tracker"
$APP_NAME         = "candidate-tracker-api"
$IMAGE_TAG        = "v1"
```

Generate the two secrets rather than inventing them:

```powershell
$PG_PASSWORD = python -c "import secrets; print(secrets.token_urlsafe(24))"
$JWT_SECRET  = python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Do not reuse the local `.env` values. The local key has been on a development
machine and in shell history; production gets its own.

---

## 5. Deployment steps

### 5.1 Resource group

```powershell
az group create --name $RESOURCE_GROUP --location $LOCATION
```

Everything below lives in this group, so teardown is a single command later.

### 5.2 PostgreSQL flexible server

```powershell
az postgres flexible-server create `
  --resource-group $RESOURCE_GROUP `
  --name $PG_SERVER `
  --location $LOCATION `
  --admin-user $PG_ADMIN `
  --admin-password $PG_PASSWORD `
  --tier Burstable `
  --sku-name Standard_B1ms `
  --storage-size 32 `
  --version 16 `
  --public-access 0.0.0.0 `
  --yes
```

`--public-access 0.0.0.0` creates a firewall rule permitting connections from
Azure-internal addresses, which is how the container app reaches the database.

This is a deliberate trade-off and worth stating plainly: that rule allows any
Azure resource, including those in other subscriptions, to attempt a connection.
Authentication still applies, so it is not open access, but it is broader than
necessary. The correct production answer is private access via VNet integration,
which places the server on a private network with no public endpoint. VNet
integration is out of scope for this exercise; it is the first thing to change
before this handles real data.

Create the application database:

```powershell
az postgres flexible-server db create `
  --resource-group $RESOURCE_GROUP `
  --server-name $PG_SERVER `
  --database-name $PG_DATABASE
```

Server creation takes several minutes.

### 5.3 Container registry

```powershell
az acr create `
  --resource-group $RESOURCE_GROUP `
  --name $ACR_NAME `
  --sku Basic `
  --admin-enabled true
```

Admin credentials are the simplest way to let Container Apps pull the image.
Managed identity is the better answer and avoids a stored password entirely;
admin user is used here to keep the deployment to one dimension of new material.

### 5.4 Build and push the image

```powershell
az acr build `
  --registry $ACR_NAME `
  --image "$APP_NAME`:$IMAGE_TAG" `
  --file Dockerfile `
  .
```

`az acr build` builds in Azure rather than locally, so the image is produced on
Linux/amd64 regardless of the host. This matters: an image built on an ARM
machine will not start on Container Apps.

The build respects `.dockerignore`, so `.env`, `.git`, and `tests/` are excluded
from the uploaded context.

### 5.5 Container Apps environment

```powershell
az containerapp env create `
  --name $ENVIRONMENT `
  --resource-group $RESOURCE_GROUP `
  --location $LOCATION
```

This provisions the shared environment and its Log Analytics workspace, which is
what makes `az containerapp logs` work later.

### 5.6 Create the container app

Retrieve registry credentials and the database hostname:

```powershell
$ACR_SERVER   = az acr show --name $ACR_NAME --query loginServer --output tsv
$ACR_USERNAME = az acr credential show --name $ACR_NAME --query username --output tsv
$ACR_PASSWORD = az acr credential show --name $ACR_NAME --query "passwords[0].value" --output tsv
$PG_HOST      = az postgres flexible-server show --resource-group $RESOURCE_GROUP --name $PG_SERVER --query fullyQualifiedDomainName --output tsv
```

Create the app:

```powershell
az containerapp create `
  --name $APP_NAME `
  --resource-group $RESOURCE_GROUP `
  --environment $ENVIRONMENT `
  --image "$ACR_SERVER/$APP_NAME`:$IMAGE_TAG" `
  --registry-server $ACR_SERVER `
  --registry-username $ACR_USERNAME `
  --registry-password $ACR_PASSWORD `
  --target-port 8000 `
  --ingress external `
  --min-replicas 1 `
  --max-replicas 1 `
  --secrets "pg-password=$PG_PASSWORD" "jwt-secret=$JWT_SECRET" `
  --env-vars `
    "POSTGRES_HOST=$PG_HOST" `
    "POSTGRES_PORT=5432" `
    "POSTGRES_USER=$PG_ADMIN" `
    "POSTGRES_DB=$PG_DATABASE" `
    "POSTGRES_SSLMODE=require" `
    "POSTGRES_PASSWORD=secretref:pg-password" `
    "JWT_SECRET_KEY=secretref:jwt-secret" `
    "JWT_ALGORITHM=HS256" `
    "ACCESS_TOKEN_EXPIRE_MINUTES=30" `
  --command "/bin/sh" `
  --args "-c","alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000" `
  --query "properties.configuration.ingress.fqdn" `
  --output tsv
```

`--min-replicas 1` prevents scale-to-zero. Scale-to-zero saves money but adds a
cold-start delay on the first request after idle, and with migrations in the
startup command that delay is significant.

The final line prints the public FQDN. Record it.

---

## 6. How secrets are handled

Three storage locations, none of them source control:

| Stage | Where secrets live | Committed? |
| --- | --- | --- |
| Local dev | `.env` | No — `.gitignore` line 1 |
| Local reference | `.env.example`, placeholder values only | Yes |
| Azure | Container Apps secrets | No |

`app/config.py` declares `postgres_password` and `jwt_secret_key` with **no
default value**. If either is absent from the environment, pydantic-settings
raises a validation error at import and the process exits. There is no fallback
key, so a misconfigured deployment fails loudly at startup instead of running
with a publicly known signing key.

`secretref:` in `--env-vars` binds an environment variable to a stored secret.
The value is injected at runtime and is not visible in `az containerapp show`
output or the portal's configuration view.

**Path to Key Vault.** Container Apps secrets are adequate but flat: no
versioning, no rotation policy, no audit trail of access. The next step is
Azure Key Vault with a user-assigned managed identity, which also removes the
ACR admin password from section 5.6. Deferred, not overlooked.

**Rotation.** `az containerapp secret set` updates a value; the app must then be
restarted (`az containerapp revision restart`) to pick it up. Rotating
`jwt-secret` invalidates every issued token, forcing all users to log in again —
which is the intended behaviour if the key is believed compromised.

---

## 7. Verification

### Reachability

```powershell
$FQDN = az containerapp show --name $APP_NAME --resource-group $RESOURCE_GROUP --query "properties.configuration.ingress.fqdn" --output tsv
curl.exe "https://$FQDN/health"
```

Expect `{"status":"ok","candidates":0}`. This endpoint queries the database, so
a 200 proves the API is running **and** connected — a 503 means the app is up
but the database is not reachable, which is the handler in `main.py` doing its
job.

### Interactive docs

Open `https://<FQDN>/docs`.

### End-to-end auth

```powershell
curl.exe -X POST "https://$FQDN/auth/signup" -H "Content-Type: application/json" -d '{\"email\":\"check@example.com\",\"password\":\"a-real-password\"}'
curl.exe -X POST "https://$FQDN/auth/login" -d "username=check@example.com&password=a-real-password"
```

The second returns a bearer token. Note signup is rate-limited to 5/hour.

### Logs

```powershell
az containerapp logs show --name $APP_NAME --resource-group $RESOURCE_GROUP --follow
```

A healthy startup shows Alembic applying each migration, then:

```
INFO:     Started server process [1]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

`PYTHONUNBUFFERED=1` in the Dockerfile is what makes these appear promptly
rather than sitting in Python's stdout buffer.

Historical logs, as opposed to the live stream:

```powershell
az containerapp logs show --name $APP_NAME --resource-group $RESOURCE_GROUP --tail 100
```

---

## 8. Updating a deployed app

```powershell
az acr build --registry $ACR_NAME --image "$APP_NAME`:v2" --file Dockerfile .

az containerapp update `
  --name $APP_NAME `
  --resource-group $RESOURCE_GROUP `
  --image "$ACR_SERVER/$APP_NAME`:v2"
```

Each update creates a new **revision**. Traffic moves to it once it reports
healthy; the previous revision is retained.

Use explicit version tags rather than `latest`. `latest` makes it impossible to
tell which build is running and breaks rollback, because the tag no longer
identifies a specific image.

---

## 9. Rollback

```powershell
az containerapp revision list --name $APP_NAME --resource-group $RESOURCE_GROUP --output table

az containerapp revision activate `
  --name $APP_NAME `
  --resource-group $RESOURCE_GROUP `
  --revision <previous-revision-name>
```

**Caveat.** This rolls back the application only. Alembic migrations are not
reversed, so a revision whose migration dropped or altered a column may not run
correctly against the migrated schema. Any migration that destroys data needs a
tested `downgrade()` and a database restore point before it is deployed.

---

## 10. Teardown

```powershell
az group delete --name $RESOURCE_GROUP --yes --no-wait
```

Deletes every resource in the group. Run this once the deployment has been
reviewed — the PostgreSQL server bills continuously whether or not the app
receives traffic.

Confirm afterwards with `az group list --output table`.

---

## 11. Known limitations

1. **Database firewall is open to Azure.** Should be VNet private access.
2. **ACR admin credentials** rather than managed identity.
3. **Secrets in Container Apps, not Key Vault** — no rotation policy or audit trail.
4. **Migrations at startup** — will not survive scaling past one replica.
5. **Single replica, no HA** — Burstable tier with no standby.
6. **Rate limiting is per-instance and in-memory** — resets on restart, and would
   not be shared across replicas.
7. **No CI/CD to Azure** — `.github/workflows/test.yml` runs tests but does not
   deploy. Deployment is manual and therefore repeatable only by following this
   document.
