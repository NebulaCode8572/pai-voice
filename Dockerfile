FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAIVOICE_HOST=0.0.0.0

WORKDIR /app

COPY packages/realtime-core/requirements-voice.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/realtime-core/ ./

EXPOSE 8780
CMD ["python", "server.py"]
