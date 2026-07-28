FROM python:3.11-slim

WORKDIR /app

COPY api/requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY api api
COPY web web
COPY scraper scraper

# api/kazi_submissions.db lives here — mount a persistent volume at /app/api
# in production or every deploy wipes employer submissions.
# Port 8080 to match what Fly's GitHub-connected deploy expects by default.
EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
