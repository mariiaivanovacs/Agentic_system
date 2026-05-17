FROM python:3.10-slim
WORKDIR /app
RUN pip install --no-cache-dir pyyaml "pyjwt[crypto]" google-cloud-storage
COPY sandbox_task.py code_sandbox_task.py ./
# SANDBOX_TASK selects the entry point at runtime (default: sandbox_task.py)
CMD ["sh", "-c", "python ${SANDBOX_TASK:-sandbox_task.py}"]
