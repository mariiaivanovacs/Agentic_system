FROM python:3.10-slim
WORKDIR /app
RUN pip install --no-cache-dir pyyaml
COPY sandbox_task.py .
CMD ["python", "sandbox_task.py"]
