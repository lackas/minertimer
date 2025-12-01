FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY minertimer.py /app/

EXPOSE 8000

CMD ["gunicorn", "-b", "0.0.0.0:8000", "minertimer:app"]
