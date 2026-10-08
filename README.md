# XPE ENGINE ⭐

Motores y automatizaciones de infraestructura de **XPE Agent** (por Yostin Martinez).

## Contenido

- `engine/` — Motor de inferencia XPE Engine: servidor OpenAI-compatible basado en llama.cpp (Docker). Diseñado para Hugging Face Spaces (cpu-basic) o cualquier Docker host (Oracle Cloud A1, etc.). Modelo: Qwen2.5-0.5B cuantizado, expuesto como "xpe".
- `oracle-hunter/` — Automatización Oracle Cloud Always Free:
  - `launch_xpe_vm.py`: crea red (VCN, subred, firewall 22/80/443) y lanza la VM ARM A1 4 OCPU/24GB con Docker preinstalado.
  - `deploy_xpe_vm.py`: despliega las réplicas de XPE Agent en la VM (docker compose, escala N réplicas, health check).

## Requisitos
- Oracle: config OCI en `~/.oci/config` o ruta propia (NO se incluyen credenciales en este repo).
- Engine: Docker o Hugging Face Space con SDK docker, puerto 7860.

## Nota
Los subagentes de XPE Agent (Arquitecto/Worker/Integrador) viven en el repo principal XPE-AGENT (`src/services/`).
