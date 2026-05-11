FROM python:3.13-alpine
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
RUN python vector_store/embed_policies.py
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-5001}"]
