FROM mullenkamp/wrf-wps-wvt-ref-debian:1.0

WORKDIR /app

COPY pyproject.toml README.md ./
RUN uv pip install --no-cache-dir . && rm pyproject.toml README.md

# ⚠ The pipeline is VENDORED, not taken from wrf-auto-runs/. This image is the provenance
# reference for the WVT method; its orchestration must not drift with the working tree, and a
# `COPY wrf-auto-runs/*.py` here would silently rebuild it against four months of unrelated change.
# Two of those changes are outright incompatible with WRF 4.3.3 + the authors' modules:
#   * set_params.py emits num_wvt_regions / num_wvt_bdy_regions when tracer_opt=4 — neither is in
#     the authors' registry, so WRF rejects the namelist;
#   * create_trmask.py refuses the flat min_lat/max_lat/min_lon/max_lon keys this image's configs use.
# pipeline/ is the :1.2 pipeline plus exactly two forward-ported fixes — see FREEZE.md.
COPY gfortran_wvt_ref/pipeline/*.py .

CMD ["python", "-u", "main.py"]
