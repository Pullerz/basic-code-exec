FROM python:3.11-slim

WORKDIR /app

# Install SWI-Prolog (swipl)
RUN apt-get update \
    && apt-get install -y --no-install-recommends swi-prolog \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY container-requirements.txt ./
RUN pip install --no-cache-dir -r container-requirements.txt

COPY main.py ./

EXPOSE 6000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "6000", "--reload"]
