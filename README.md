# document-service

Microservicio FastAPI para ingesta de PDFs y búsqueda semántica (RAG). Forma parte del stack del tutor IA junto con `auth-service` y `chat-service`.

## Qué hace

1. **Ingesta**: recibe un PDF, extrae el texto página por página, lo divide en chunks, genera embeddings con Cloudflare Workers AI y los guarda en PostgreSQL + pgvector.
2. **Búsqueda**: dado un query de texto, lo embebe y devuelve los chunks más similares del usuario, listos para inyectarse como contexto en el prompt del LLM.
3. **Deduplicación**: calcula SHA-256 del archivo antes de procesarlo; si el usuario ya subió el mismo archivo, devuelve el `document_id` existente sin reprocesar.

## Endpoints

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| `GET` | `/health` | — | Estado del servicio |
| `POST` | `/documents/upload` | JWT usuario | Sube uno o más PDFs |
| `GET` | `/documents` | JWT usuario | Lista documentos del usuario |
| `DELETE` | `/documents/{id}` | JWT usuario | Elimina un documento y sus chunks |
| `POST` | `/documents/search` | JWT servicio | Búsqueda semántica (llamado por chat-service) |

### POST /documents/upload

```json
// multipart/form-data — campo "files"
// Respuesta
[
  {
    "document_id": 42,
    "filename": "apunte.pdf",
    "status": "ready",       // "ready" | "duplicate" | "error"
    "chunk_count": 38,
    "page_count": 12,
    "file_type": "pdf"
  }
]
```

### POST /documents/search

```json
// Request
{
  "query": "¿Qué es un número complejo?",
  "user_email": "usuario@ejemplo.com",
  "top_k": 5,
  "similarity_threshold": 0.72
}

// Respuesta
{
  "query": "¿Qué es un número complejo?",
  "found": 3,
  "results": [
    {
      "chunk_text": "Un número complejo es...",
      "filename": "apunte.pdf",
      "page_number": 4,
      "similarity": 0.891,
      "document_id": 42,
      "metadata": null
    }
  ]
}
```

## Autenticación

Todos los endpoints requieren un JWT en el header `Authorization: Bearer <token>`.

- **Endpoints de usuario** (`/upload`, `GET /documents`, `DELETE`): token firmado por `auth-service` con el email del usuario en el claim `sub`.
- **Endpoint de búsqueda** (`/search`): acepta también tokens de servicio generados por `chat-service` (algoritmo HS512, subject `service@chat-service.internal`).

Ambos tipos usan la misma clave secreta (`JWT_SECRET`).

## Stack técnico

| Componente | Tecnología |
|---|---|
| Framework | FastAPI + uvicorn |
| Base de datos | PostgreSQL (Neon) + pgvector |
| Driver async | asyncpg |
| Embeddings | Cloudflare Workers AI — `@cf/baai/bge-base-en-v1.5` |
| Extracción PDF | PyMuPDF + pdf2image + pytesseract (OCR fallback) |
| Autenticación | python-jose (HS256 / HS512) |

## Variables de entorno

Copiar `.env.example` a `.env` y completar los valores reales.

| Variable | Descripción |
|---|---|
| `DB_HOST` | Host de PostgreSQL |
| `DB_PORT` | Puerto (default `5432`) |
| `DB_NAME` | Nombre de la base |
| `DB_USER` | Usuario |
| `DB_PASSWORD` | Contraseña |
| `DB_SSL` | Modo SSL (`disable` / `require`) |
| `CLOUDFLARE_ACCOUNT_ID` | ID de cuenta de Cloudflare |
| `CLOUDFLARE_API_TOKEN` | Token de API de Cloudflare |
| `JWT_SECRET` | Clave secreta compartida con `auth-service` y `chat-service` |
| `MAX_FILE_SIZE_MB` | Tamaño máximo por archivo (default `20`) |
| `MAX_FILES_PER_UPLOAD` | Archivos máximos por request (default `5`) |
| `SEARCH_TOP_K` | Chunks a devolver por búsqueda (default `5`) |
| `SIMILARITY_THRESHOLD` | Similitud mínima para incluir un chunk (default `0.72`) |

## Correr en local

```bash
# 1. Crear entorno virtual e instalar dependencias
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configurar variables de entorno
cp .env.example .env
# editar .env con los valores reales

# 3. Levantar
uvicorn app.main:app --host 0.0.0.0 --port 8083 --reload
```

O con Docker:

```bash
docker build -t document-service .
docker run --env-file .env -p 8083:8083 document-service
```

## Deploy (Fly.io)

El servicio corre en `https://document-service-academy.fly.dev`.

```bash
# Primer deploy
fly apps create document-service-academy
fly secrets set DB_HOST=... DB_PASSWORD=... JWT_SECRET=... -a document-service-academy
fly deploy -a document-service-academy --remote-only

# Redeploy
fly deploy -a document-service-academy --remote-only
```

## Arquitectura del pipeline de ingesta

```
PDF recibido
    │
    ▼
detect_file_kind()       ← valida que sea PDF real (magic bytes)
    │
    ▼
extract()                ← PyMuPDF extrae bloques de texto por página
    │                       fallback OCR con pytesseract si la página está vacía
    ▼
chunk_blocks()           ← divide en chunks de ~512 tokens con overlap de 64
    │
    ▼
embed_texts()            ← Cloudflare Workers AI → vector de 768 dimensiones
    │
    ▼
insert_document()        ← guarda metadata en tabla `documents`
insert_chunks()          ← guarda texto + vector en tabla `document_chunks`
```
