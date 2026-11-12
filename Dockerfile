FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --system --uid 10001 --no-create-home app \
 && mkdir -p data logs && chown app:app data logs
COPY src/ src/
COPY main.py .
ENV PYTHONUNBUFFERED=1
USER app
CMD ["python", "main.py"]
