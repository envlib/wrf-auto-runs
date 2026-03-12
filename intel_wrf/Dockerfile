FROM mullenkamp/wrf-wps-intel-ubuntu:1.0

WORKDIR /app

# RUN uv tool install toml-cli

COPY requirements.txt .
RUN uv pip install --no-cache-dir -r requirements.txt

COPY wrf-era5-auto/*.py .

CMD ["uv", "run", "python", "-u", "main.py"]

# CMD ["/bin/bash"]