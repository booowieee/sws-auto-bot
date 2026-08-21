FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HEADLESS=true \
    CHROME_PERSISTENT_PROFILE_DIR=/app/data/chrome_profile

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/chrome_profile /app/logs /app/screenshots

ENTRYPOINT ["python", "-m", "src.__main__"]
