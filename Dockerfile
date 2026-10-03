FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY data/characters/si_001.yaml ./data/characters/si_001.yaml
COPY data/worlds/si_world.yaml ./data/worlds/si_world.yaml
COPY config/si.env.example ./config/si.env.example
# Preserve the existing src-layout resource paths; install no optional extras.
RUN python -m pip install --no-cache-dir -e . && python -m pip check

CMD ["python", "-m", "evolving_companion.server", "--env-file", "config/si.env"]
