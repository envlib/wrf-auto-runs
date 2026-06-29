FROM mullenkamp/wrf-wps-wvt-mr-debian:1.0

WORKDIR /app

COPY pyproject.toml README.md ./
RUN uv pip install --no-cache-dir . && rm pyproject.toml README.md

COPY wrf-auto-runs/*.py .

CMD ["python", "-u", "main.py"]
