FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY lab ./lab
RUN mkdir /data && useradd --uid 10001 lab && chown lab /data
USER lab
ENV LAB_DB=/data/laboratory.db
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "lab.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
