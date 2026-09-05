FROM python:3.11-slim

WORKDIR /app

COPY . .

ENV PORT=7860
EXPOSE 7860

CMD ["python", "app.py"]
