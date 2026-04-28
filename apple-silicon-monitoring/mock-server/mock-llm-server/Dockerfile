FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 5901

ENV HTTP_PORT=5901
ENV NODE_ID=1
ENV MODEL_NAME=llama-3-70b
ENV OTEL_ENDPOINT=otel-agent:5911

CMD ["python", "main.py"]
