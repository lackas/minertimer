FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY web/minertimer.py /app/
RUN mkdir -p /app/assets
COPY minertimer.sh /app/assets/minertimer.sh
COPY com.soferio.minertimer_daily_timer.plist /app/assets/com.soferio.minertimer_daily_timer.plist
COPY web/assets/ /app/assets/

EXPOSE 8000

CMD ["gunicorn", "-b", "0.0.0.0:8000", "minertimer:app"]
