# Live scanner for Render (or any container host). Spectral recall driver: fits 512 MB.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libchromaprint-tools \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
# the demo world is generated from seeds (byte-identical to the build machine), then the
# waveform peaks are cached so startup never decodes the whole set
RUN pip install --no-cache-dir -e ".[pg]" \
 && python scripts/fetch_corpus.py \
 && python -c "from pathlib import Path; from setledger.app import _waveform_peaks; _waveform_peaks(Path('data/mixes/demo_set.wav'))"
ENV SETLEDGER_EMBEDDER=spectral
EXPOSE 10000
CMD ["sh", "-c", "uvicorn setledger.app:app --app-dir src --host 0.0.0.0 --port ${PORT:-10000}"]
