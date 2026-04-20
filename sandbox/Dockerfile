# =============================================================================
# sandbox/Dockerfile — AutoDebug Sandbox Image
#
# This image is used to create isolated containers for each debugging task.
# It has git, Python, Node.js, Java, Go, and Rust installed.
# =============================================================================

FROM ubuntu:22.04

LABEL maintainer="autodebug"
LABEL description="Isolated sandbox for AutoDebug task execution"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core tools
    git \
    curl \
    wget \
    ca-certificates \
    build-essential \
    # Python
    python3.11 \
    python3.11-dev \
    python3-pip \
    python3.11-venv \
    # Node.js (via NodeSource)
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    # Java
    && apt-get install -y default-jdk maven \
    # Cleanup
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Go installation
# ---------------------------------------------------------------------------
RUN wget -q https://go.dev/dl/go1.22.0.linux-amd64.tar.gz \
    && tar -C /usr/local -xzf go1.22.0.linux-amd64.tar.gz \
    && rm go1.22.0.linux-amd64.tar.gz
ENV PATH="$PATH:/usr/local/go/bin"

# ---------------------------------------------------------------------------
# Python aliases
# ---------------------------------------------------------------------------
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && pip3 install --upgrade pip setuptools wheel pytest

# ---------------------------------------------------------------------------
# Create non-root user for running tasks (security best practice)
# ---------------------------------------------------------------------------
RUN groupadd -r sandbox && useradd -r -g sandbox -m -d /home/sandbox sandbox

# ---------------------------------------------------------------------------
# Workspace directory
# ---------------------------------------------------------------------------
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace

WORKDIR /workspace

# ---------------------------------------------------------------------------
# Git safety — allow workspace to be owned by sandbox user
# ---------------------------------------------------------------------------
RUN git config --global --add safe.directory /workspace

USER sandbox

CMD ["tail", "-f", "/dev/null"]
