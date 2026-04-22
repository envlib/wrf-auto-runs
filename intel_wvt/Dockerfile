FROM mullenkamp/wrf-wps-intel-wvt-ubuntu:1.0

WORKDIR /app

RUN uv tool uninstall era5_to_int 2>/dev/null || true

COPY pyproject.toml README.md ./
RUN uv pip install --no-cache-dir . && rm pyproject.toml README.md

COPY wrf-auto-runs/*.py .

CMD ["python", "-u", "main.py"]
