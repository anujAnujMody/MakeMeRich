FROM python:3.12-slim

WORKDIR /app

ENV OPENBLAS_NUM_THREADS=2 \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN addgroup --system --gid 1001 app && \
    adduser --system --uid 1001 --ingroup app app

COPY . .
RUN chown -R app:app /app

USER app
CMD ["python", "executor.py"]
