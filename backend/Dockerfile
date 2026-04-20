# =============================================================================
# backend/Dockerfile — AutoDebug Services
#
# Runs both the FastAPI server and the background worker (depending on command).
# Also installs the Docker CLI client so the manager can talk to DinD.
# =============================================================================

FROM python:3.11-slim

LABEL maintainer="autodebug"

# ---------------------------------------------------------------------------
# Install Docker CLI so we can spawn sandbox containers (DinD)
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo \
      "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
      "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------------------------------------------------------------------------
# Python Dependencies
# ---------------------------------------------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Application Code
# ---------------------------------------------------------------------------
COPY . .

# Let the container execute backend OR worker based on command
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
