# ---- Stage 1: build the React frontend ----
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python app that serves the API and the built frontend ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/llms.db \
    FRONTEND_DIST=/app/frontend/dist \
    PORT=8000
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY --from=frontend /frontend/dist frontend/dist
# /data holds the SQLite file. Mount a volume there (docker-compose does; on Railway add one).
RUN mkdir -p /data
EXPOSE 8000
# One worker on purpose: SQLite has one writer, and the scheduler runs in-process.
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT} --workers 1"]
