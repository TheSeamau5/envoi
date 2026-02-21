FROM python:3.12-slim

WORKDIR /app
COPY . /app/

RUN pip install --no-cache-dir \
    "envoi @ git+https://github.com/TheSeamau5/envoi.git"

CMD ["envoi", "serve", "--host", "0.0.0.0", "--port", "8000"]
