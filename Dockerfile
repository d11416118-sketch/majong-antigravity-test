FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5001

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

EXPOSE 5001

HEALTHCHECK --interval=3s --timeout=5s --start-period=10s --retries=40 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/', timeout=3)"

CMD ["python", "run_server.py"]
