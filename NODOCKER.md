
# Run without Docker

This option is for a machine that already has PostgreSQL, pgvector, Redis, and Orthanc installed.

This is the setup for Fedora. For any othe distro change the commands accordingly. 

### Fedora system packages

```bash
sudo dnf install -y python3 python3-pip python3-devel gcc \
  postgresql-server postgresql-devel redis
```

Initialize and start PostgreSQL and Redis:

```bash
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql redis
```

Install pgvector if it is not provided by your PostgreSQL package. Use your distribution package or follow the official pgvector build instructions:

```bash
git clone https://github.com/pgvector/pgvector.git
cd pgvector
make
sudo make install
cd ..
```

Create the database and enable the extension:

```bash
sudo -u postgres psql -c "CREATE USER msvmed WITH PASSWORD 'msvmed';"
sudo -u postgres psql -c "CREATE DATABASE msvmed OWNER msvmed;"
sudo -u postgres psql -d msvmed -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Create the Python environment and install dependencies with uv:

```bash
uv sync --extra gui
```

Set local service addresses in the shell before starting the processes:

```bash
export DATABASE_URL=postgresql://msvmed:msvmed@localhost:5432/msvmed
export REDIS_URL=redis://localhost:6379/0
export ORTHANC_URL=http://localhost:8042
export ORTHANC_USER=orthanc
export ORTHANC_PASS=orthanc
export API_SECRET=replace_this_with_a_long_random_value
export JWT_SECRET=replace_this_with_a_different_long_random_value
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=change_this_demo_password
```

Start Orthanc, PostgreSQL, and Redis separately. Apply migrations once, then use
separate terminals for the API and queue-specific workers:
### 1. Run the migration.

```bash
uv run python -m src.migrate
```

### 2. Start the API in Terminal 1.

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Start the DICOM upload worker in Terminal 2.

```bash
uv run celery -A src.worker.celery_app worker --loglevel=info --queues=dicom --concurrency=1
```

### 4. Start the PACS forwarding worker in Terminal 3.

```bash
uv run celery -A src.worker.celery_app worker --loglevel=info --queues=forward --concurrency=1
```

### 5. Start the demo/UI worker in Terminal 4.

```bash
uv run celery -A src.worker.celery_app worker --loglevel=info --queues=demo --concurrency=1
```

### 6. Optionally start the GUI in Terminal 5.

```bash
API_URL=http://localhost:8000 API_TOKEN="$API_SECRET" uv run python gui.py
```
