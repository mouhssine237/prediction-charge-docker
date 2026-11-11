FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY predictor.py .
EXPOSE 9100
CMD ["python", "predictor.py"]
