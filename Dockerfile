FROM python:3.10-slim
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--timeout", "90"]
