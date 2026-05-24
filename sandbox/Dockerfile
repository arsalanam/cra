FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN pip install --no-cache-dir \
    pandas==2.2.* \
    numpy==1.26.* \
    scipy==1.13.* \
    statsmodels==0.14.* \
    matplotlib==3.9.* \
    seaborn==0.13.* \
    forestplot==0.4.*

# Non-root user for execution
RUN useradd --create-home sandbox
USER sandbox
WORKDIR /home/sandbox

# Fixed I/O paths — host bind-mounts here
RUN mkdir -p /home/sandbox/input /home/sandbox/output

ENTRYPOINT ["python", "/home/sandbox/script.py"]
