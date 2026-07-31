# Tempris JAStudio backend. CPU by default — see docker-compose.gpu.yml for a
# CUDA-enabled override. Model weights are NOT baked into the image; mount
# ./data/models as a volume (see docker-compose.yml).
FROM python:3.12-slim

# WeasyPrint (PDF export) needs Pango/Cairo; LibreOffice headless converts
# legacy .doc uploads to .docx before parsing (see services/ingestion).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf2.0-0 \
    libreoffice --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV APP_HOST=0.0.0.0
ENV APP_PORT=9400
EXPOSE 9400

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9400"]
