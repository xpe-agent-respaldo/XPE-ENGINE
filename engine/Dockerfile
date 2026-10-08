FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl

WORKDIR /app

RUN pip install --no-cache-dir "llama-cpp-python[server]"

RUN curl -L -o /app/model.gguf "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

EXPOSE 7860

CMD ["python3", "-m", "llama_cpp.server", "--model", "/app/model.gguf", "--host", "0.0.0.0", "--port", "7860", "--n_ctx", "2048", "--model_alias", "xpe"]
