# OpenAPI 2.0 spec template for Cloud API Gateway
# This is rendered by Terraform's templatefile() function.
swagger: "2.0"
info:
  title: "LLMOps API"
  description: "API Gateway for LLMOps serving layer"
  version: "1.0.0"
host: ""
schemes:
  - "https"
produces:
  - "application/json"

x-google-backend:
  address: "${cloud_run_url}"
  protocol: h2

paths:
  /health:
    get:
      summary: "Health check"
      operationId: "healthCheck"
      responses:
        200:
          description: "OK"

  /ready:
    get:
      summary: "Readiness check"
      operationId: "readyCheck"
      responses:
        200:
          description: "OK"

  /chat:
    post:
      summary: "Chat endpoint"
      operationId: "chat"
      security:
        - api_key: []
      parameters:
        - in: body
          name: body
          required: true
          schema:
            type: object
            properties:
              message:
                type: string
              session_id:
                type: string
      responses:
        200:
          description: "Chat response"

  /feedback:
    post:
      summary: "Submit feedback"
      operationId: "feedback"
      security:
        - api_key: []
      parameters:
        - in: body
          name: body
          required: true
          schema:
            type: object
      responses:
        200:
          description: "Feedback recorded"

  /manifest:
    get:
      summary: "Pipeline artifact manifest"
      operationId: "manifest"
      security:
        - api_key: []
      responses:
        200:
          description: "Manifest data"

  /costs:
    get:
      summary: "Cost summary"
      operationId: "costs"
      security:
        - api_key: []
      responses:
        200:
          description: "Cost data"

securityDefinitions:
  api_key:
    type: apiKey
    name: x-api-key
    in: header
