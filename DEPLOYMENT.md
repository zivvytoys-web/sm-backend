# Railway deployment

Deploy `smmai-backend` as the Railway service root. The included Dockerfile
uses Railway's `PORT`, waits for the database at startup, creates the ORM
tables, and then runs the idempotent seed check.

Create a Railway MySQL service and expose its `MYSQL_URL` to the backend, or
set `DATABASE_URL` yourself in this form:

```text
mysql+pymysql://USER:PASSWORD@HOST:PORT/DATABASE
```

Set these Railway variables before deploying:

```text
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=<long random value>
ENCRYPTION_KEY=<different long random value>
FRONTEND_URL=https://YOUR-VERCEL-PROJECT.vercel.app
BACKEND_CORS_ORIGINS=["https://YOUR-VERCEL-PROJECT.vercel.app"]
CORS_ORIGIN_REGEX=
SEED_DEFAULT_ADMIN=true
DEFAULT_ADMIN_EMAIL=admin@example.com
DEFAULT_ADMIN_PASSWORD=<strong unique password>
DEFAULT_ADMIN_NAME=Platform Administrator
```

`SEED_DEFAULT_ADMIN` creates the account only if that email is absent, so a
redeploy never overwrites its password or any existing Railway data. Do not
put the administrator password in Git or a Vercel variable.

For persistent media on Railway, configure Cloudinary (`CLOUDINARY_CLOUD_NAME`,
`CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`). Railway's local filesystem is
ephemeral, so local uploads are lost after a redeploy.

After deployment, open `https://YOUR-RAILWAY-DOMAIN/docs` and verify the root
health endpoint returns `healthy`.
