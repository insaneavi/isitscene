FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/isitscene
RUN useradd --system --uid 1000 --create-home isitscene
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /config /movies && chown -R isitscene:isitscene /opt/isitscene /config
USER isitscene
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080"]
