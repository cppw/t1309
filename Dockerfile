FROM python:3.11-slim

WORKDIR /app

# Install dependencies first so Docker can cache this layer
# when only y.py changes on later builds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the app (y.py, README.md, etc.)
COPY . .

# Railway sets PORT at runtime; your app already reads it via
# os.environ.get("PORT", 10000), so no EXPOSE is strictly required,
# but it documents intent for local `docker run`
EXPOSE 10000

CMD ["python", "y.py"]
