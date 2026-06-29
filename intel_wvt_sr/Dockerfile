FROM mullenkamp/wrf-wps-intel-wvt-sr-ubuntu:1.0

# This is the single-region image: its WRF registry declares TRMASK as 2-D (ij), so create_trmask
# must write a flat TRMASK(Time, sn, we) rather than the multi-region region-dimensioned layout.
ENV WVT_TRMASK_2D=1

WORKDIR /app

RUN uv tool uninstall era5_to_int 2>/dev/null || true

COPY pyproject.toml README.md ./
RUN uv pip install --no-cache-dir . && rm pyproject.toml README.md

COPY wrf-auto-runs/*.py .

CMD ["python", "-u", "main.py"]
