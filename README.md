# LLMOps Pipeline

General-purpose, config-driven LLMOps pipeline on GCP.

Change the YAML config → the entire pipeline adapts to any LLM use case (RAG, Agent, Copilot).

## Quick Start

```bash
# Install
poetry install

# Run a pipeline job
llmops confs/feature_engineering.yaml

# Run master pipeline on Vertex AI
python -m llmops_pipeline.kfp_pipelines.master --config confs/feature_engineering.yaml
```

See `docs/` for full documentation.
