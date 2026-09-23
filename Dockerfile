FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 DATABASE_PATH=/var/lib/careerquest/careerquest.sqlite3
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 careerquest && mkdir -p /var/lib/careerquest && chown careerquest:careerquest /var/lib/careerquest
COPY app ./app
COPY src ./src
COPY data ./data
COPY index.html run.py ./
USER careerquest
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/healthz',timeout=3)"
CMD ["python", "run.py"]
