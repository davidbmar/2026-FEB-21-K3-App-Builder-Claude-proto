FROM python:3.12-slim
WORKDIR /app
ARG APP_NAME=myapp
ENV APP_NAME=$APP_NAME
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir
COPY . .
CMD ["python", "job.py"]
