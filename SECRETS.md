# Creating the secrets Cloud Run expects

`deploy.sh` reads every secret from Secret Manager, so nothing sensitive is baked into
the image or passed on a command line that lands in shell history.

```bash
export GCP_PROJECT=your-project
gcloud services enable secretmanager.googleapis.com run.googleapis.com --project "$GCP_PROJECT"

# Generate the two JWT signing keys fresh — never reuse the dev values.
create() { printf '%s' "$2" | gcloud secrets create "$1" --data-file=- --project "$GCP_PROJECT" \
  2>/dev/null || printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=- --project "$GCP_PROJECT"; }

create hireiq-jwt-secret            "$(openssl rand -hex 32)"
create hireiq-employer-jwt-secret   "$(openssl rand -hex 32)"
create hireiq-digest-token          "$(openssl rand -hex 32)"

# From your own accounts.
create hireiq-gemini-key            "$GEMINI_API_KEY"
create hireiq-agora-cert            "$AGORA_APP_CERTIFICATE"
create hireiq-agora-customer-id     "$AGORA_CUSTOMER_ID"
create hireiq-agora-customer-secret "$AGORA_CUSTOMER_SECRET"

# Grant the runtime service account read access.
SA="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for s in hireiq-jwt-secret hireiq-employer-jwt-secret hireiq-digest-token \
         hireiq-gemini-key hireiq-agora-cert hireiq-agora-customer-id \
         hireiq-agora-customer-secret; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor --project "$GCP_PROJECT" --quiet
done

# Then:
export AGORA_APP_ID=your_32_char_app_id
./deploy.sh
```

## The one thing to decide before real use

`DATABASE_URL` defaults to SQLite **inside the container**, which means the database is
lost on every revision and cannot be shared between instances. That is fine for a demo
and wrong for anything else. For real use, provision Cloud SQL Postgres and pass its
connection string — the models are unchanged.

`--max-instances 1` is a demo constraint, not a scaling story: live sessions hold
in-memory runtime state and an in-process SSE broadcast. Multi-instance is a Redis
pub/sub swap behind the same two interfaces, with no change to the API contract.
