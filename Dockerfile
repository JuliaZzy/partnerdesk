# API image: FastAPI + Alembic + tests/lint/catalog. Source is bind-mounted in compose
# so `--reload` and `compose run` see the tree on disk; PYTHONPATH keeps imports on /app
# even when the editable .egg-info from the image layer is hidden by the mount.
#
# PYTHON_IMAGE / PIP_INDEX_URL are compose build-args: Docker Hub is often unreachable
# from campus networks, so compose.yaml defaults to a mirror.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY partnerdesk ./partnerdesk
COPY scripts ./scripts
COPY tests ./tests
COPY evals ./evals

ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" -e ".[contract,dev,catalog]"

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "partnerdesk.app:app", "--host", "0.0.0.0", "--port", "8000"]
