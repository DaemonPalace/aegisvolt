# One image recipe for all 4 services (ported from wandersync services/*/Dockerfile).
# docker build --build-arg SERVICE=billing -f services/Dockerfile services
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG SERVICE
COPY common/ common/
COPY ${SERVICE}/ .

USER app
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
