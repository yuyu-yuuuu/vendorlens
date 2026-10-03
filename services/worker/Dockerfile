FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY skills ./skills
RUN pip install --no-cache-dir .
CMD ["python", "-m", "vendorlens.worker"]
