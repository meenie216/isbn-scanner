# ISBN Scanner

Mobile-friendly barcode scanning app for cataloguing books and DVDs/Blu-ray into boxes at named locations.

## Architecture

```
Browser (S3 + CloudFront)
  → API Gateway (HTTP)
    → Lambda: scan_handler   POST /scan        (writes pending record, queues lookup)
    → Lambda: get_scan       GET  /scan/{id}   (polling endpoint)
    → Lambda: list_items     GET  /items        (browse catalogue)
  → SQS → Lambda: lookup_worker               (calls Open Library / OMDB, writes to Neon)
  → Neon PostgreSQL (serverless, no VPC needed)
```

**Lookup services (both free)**
- Books (ISBN-13): [Open Library API](https://openlibrary.org/developers/api) → Google Books fallback
- DVDs/Movies/TV (UPC/EAN): [OMDB API](https://www.omdbapi.com/) (1,000 req/day free)

---

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) configured (`aws configure`)
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Python 3.12
- A [Neon](https://neon.tech) account (free tier is sufficient)
- A free [OMDB API key](https://www.omdbapi.com/apikey.aspx) (register with email)
- (Optional) A [Google Books API key](https://developers.google.com/books/docs/v1/using#APIKey) for ISBN fallback

---

## Setup

### 1. Create the Neon database

1. Sign up at [neon.tech](https://neon.tech) and create a project (e.g. `isbn-scanner`).
2. In the Neon console, go to **Connection Details** and copy the **pooled connection string** (the one ending in `-pooler.neon.tech`). It looks like:
   ```
   postgresql://user:password@ep-xxx-pooler.eu-west-1.aws.neon.tech/isbn_scanner?sslmode=require
   ```
3. Connect with DBeaver (or `psql`) using the direct (non-pooled) connection string and run the schema:
   ```
   psql "postgresql://..." -f sql/schema.sql
   ```

### 2. Store secrets in AWS SSM Parameter Store

```bash
# Neon pooled connection string
aws ssm put-parameter \
  --name "/isbn-scanner/db-url" \
  --value "postgresql://user:password@ep-xxx-pooler.neon.tech/isbn_scanner?sslmode=require" \
  --type SecureString

# OMDB API key (required for DVD lookup)
aws ssm put-parameter \
  --name "/isbn-scanner/omdb-api-key" \
  --value "your-omdb-api-key" \
  --type String

# Google Books API key (optional)
aws ssm put-parameter \
  --name "/isbn-scanner/google-books-api-key" \
  --value "your-google-books-key" \
  --type String
```

### 3. Build and deploy

```bash
cd isbn_scanner

# Build Lambda packages and layer
sam build

# Deploy (first time — interactive)
sam deploy --guided

# Subsequent deploys
sam deploy
```

SAM will output:
- **ApiUrl** — your API Gateway base URL
- **FrontendUrl** — your CloudFront URL

### 4. Configure the frontend API URL

Edit `frontend/js/app.js` line 12 and set your API Gateway URL, **or** add a
`<script>` tag before loading `app.js` in `index.html`:

```html
<script>window.API_BASE = "https://xxxxxxxxxx.execute-api.eu-west-1.amazonaws.com/prod";</script>
```

### 5. Upload the frontend to S3

```bash
# Get the bucket name from CloudFormation outputs
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name isbn-scanner \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucket'].OutputValue" \
  --output text)

aws s3 sync frontend/ s3://$BUCKET/ --delete
```

Open the **FrontendUrl** from the SAM outputs in your browser (or on your phone).

---

## Local development

### Run a Lambda function locally

```bash
# Start a local API (uses Docker)
sam local start-api --env-vars env.json

# env.json example
{
  "ScanHandlerFunction": {
    "DB_URL": "postgresql://user:password@ep-xxx-pooler.neon.tech/isbn_scanner?sslmode=require",
    "SCAN_QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/123456789/isbn-scanner-scan-queue-prod",
    "OMDB_API_KEY": "your-key",
    "GOOGLE_BOOKS_API_KEY": "your-key"
  }
}
```

### Serve the frontend locally

```bash
cd frontend
python3 -m http.server 8080
```

Open `http://localhost:8080` — CORS is pre-configured to allow this origin.

---

## Database access with DBeaver

Use the **direct** (non-pooled) Neon connection string in DBeaver:

| Field    | Value |
|----------|-------|
| Host     | `ep-xxx.eu-west-1.aws.neon.tech` |
| Port     | `5432` |
| Database | `isbn_scanner` |
| Username | your Neon user |
| Password | your Neon password |
| SSL      | Required |

Tables: `scan_records`, `books`, `dvds`

### Useful queries

```sql
-- All items in a box
SELECT s.barcode, s.box_number, s.location,
       COALESCE(b.title, d.title) AS title,
       s.media_type
FROM   scan_records s
LEFT JOIN books b ON s.item_table = 'books' AND s.item_id = b.id
LEFT JOIN dvds  d ON s.item_table = 'dvds'  AND s.item_id = d.id
WHERE  s.box_number = 'B12'
ORDER BY s.scanned_at DESC;

-- Failed lookups
SELECT barcode, scanned_at, error_msg FROM scan_records WHERE status = 'not_found';

-- Export everything to CSV (run in DBeaver or psql)
COPY (SELECT * FROM scan_records) TO '/tmp/scans.csv' CSV HEADER;
```

---

## Project structure

```
isbn_scanner/
├── template.yaml                 AWS SAM template
├── samconfig.toml                SAM deploy config
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── scanner.js            ZXing-js camera scanning
│       └── app.js                UI logic, API calls, polling
├── backend/
│   ├── requirements.txt
│   ├── layer/requirements.txt    Lambda layer deps (psycopg2, requests)
│   ├── shared/
│   │   ├── db.py                 Neon connection helper
│   │   └── lookup.py             Open Library + OMDB lookup
│   ├── scan_handler/app.py       POST /scan
│   ├── lookup_worker/app.py      SQS → ISBN lookup → DB
│   ├── get_scan/app.py           GET /scan/{id}
│   └── list_items/app.py         GET /items
└── sql/
    └── schema.sql                PostgreSQL schema
```
