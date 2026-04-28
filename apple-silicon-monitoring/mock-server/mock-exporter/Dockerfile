FROM python:3.11-slim

WORKDIR /app

COPY main.py .

EXPOSE 5900

ENV PORT=5900
ENV NODE_ID=1

CMD ["python", "main.py"]
