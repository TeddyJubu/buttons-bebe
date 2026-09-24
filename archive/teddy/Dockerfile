FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .
COPY skills/ skills/

# These are mounted at runtime (not baked in — keeps secrets out of the image):
#   -v /root/teddy/.env:/app/.env:ro
#   -v /root/teddy/kb:/app/kb:ro
#   -v /root/shopify:/app/shopify:ro
#   -v /root/teddy/log.jsonl:/app/log.jsonl

EXPOSE 8000

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8000", "--timeout", "30", "agent:app"]
