FROM python:3.14-slim

# Installation de Tesseract OCR et du pack langue française (fra)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fra \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installation des dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du reste du code
COPY . .

# Adaptez la commande ci-dessous à votre application (Streamlit, Flask, FastAPI, etc.)
CMD ["streamlit", "run", "app.py", "--server.port=10000", "--server.address=0.0.0.0"]
