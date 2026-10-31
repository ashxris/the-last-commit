FROM python:3.12-slim

# the backend shells out to `git clone` for exhumations
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# daily counters live here (App Service mounts /home persistently)
ENV LASTCOMMIT_DATA_DIR=/home/data
RUN mkdir -p /data

EXPOSE 8000
CMD ["sh", "-c", "lastcommit web --host 0.0.0.0 --port ${PORT:-8000}"]
