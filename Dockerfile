FROM python:3.11-slim
WORKDIR /app

# Install dependencies
COPY services/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy all services + shared library
COPY services /app/services

# SERVICE_NAME determines which to run; PORT determines bind port
ENV SERVICE_NAME=nrf
ENV PORT=8001

WORKDIR /app/services
CMD uvicorn ${SERVICE_NAME}.main:app --host 0.0.0.0 --port ${PORT}
