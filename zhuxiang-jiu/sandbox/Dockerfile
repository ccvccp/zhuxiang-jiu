FROM python:3.12-slim
WORKDIR /sandbox
COPY sandbox_app.py /sandbox/sandbox_app.py
USER 65534:65534
EXPOSE 8900
CMD ["python", "-I", "-S", "/sandbox/sandbox_app.py"]
