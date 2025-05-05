from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from typing import Any, Optional
from google.cloud import bigquery
from google.oauth2 import service_account
import logging
import os
import uvicorn
import json
# --- Add these imports for SSE transport ---
from mcp.server.sse import SseServerTransport
from starlette.routing import Mount

# --- Logging Setup ---
logger = logging.getLogger('mcp_bigquery_server')
handler_stdout = logging.StreamHandler()
handler_file = logging.FileHandler('/tmp/mcp_bigquery_server.log')
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler_stdout.setFormatter(formatter)
handler_file.setFormatter(formatter)
logger.addHandler(handler_stdout)
logger.addHandler(handler_file)
logger.setLevel(logging.DEBUG)
logger.info("Starting MCP BigQuery Server (FastMCP)")

# --- FastAPI and FastMCP Setup ---
app = FastAPI(title="MCP BigQuery Server", description="BigQuery MCP server with FastMCP transport", version="0.3.0")
mcp = FastMCP("bigquery")

# --- BigQuery Database Helper ---
class BigQueryDatabase:
    def __init__(self, project: str, location: str, key_file: Optional[str], datasets_filter: list[str]):
        logger.info(f"Initializing BigQuery client for project: {project}, location: {location}, key_file: {key_file}")
        if not project:
            raise ValueError("Project is required")
        if not location:
            raise ValueError("Location is required")
        credentials = None
        if key_file:
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    key_file,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
            except Exception as e:
                logger.error(f"Error loading service account credentials: {e}")
                raise ValueError(f"Invalid key file: {e}")
        self.client = bigquery.Client(credentials=credentials, project=project, location=location)
        self.datasets_filter = datasets_filter

    def execute_query(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        logger.debug(f"Executing query: {query}")
        try:
            if params:
                job = self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params))
            else:
                job = self.client.query(query)
            results = job.result()
            rows = [dict(row.items()) for row in results]
            logger.debug(f"Query returned {len(rows)} rows")
            return rows
        except Exception as e:
            logger.error(f"Database error executing query: {e}")
            raise

    def list_tables(self) -> list[str]:
        logger.debug("Listing all tables")
        if self.datasets_filter:
            datasets = [self.client.dataset(dataset) for dataset in self.datasets_filter]
        else:
            datasets = list(self.client.list_datasets())
        logger.debug(f"Found {len(datasets)} datasets")
        tables = []
        for dataset in datasets:
            dataset_tables = self.client.list_tables(dataset.dataset_id)
            tables.extend([
                f"{dataset.dataset_id}.{table.table_id}" for table in dataset_tables
            ])
        logger.debug(f"Found {len(tables)} tables")
        return tables

    def describe_table(self, table_name: str) -> list[dict[str, Any]]:
        logger.debug(f"Describing table: {table_name}")
        parts = table_name.split(".")
        if len(parts) != 2:
            raise ValueError(f"Invalid table name: {table_name}")
        dataset_id, table_id = parts
        query = f"""
            SELECT ddl
            FROM {dataset_id}.INFORMATION_SCHEMA.TABLES
            WHERE table_name = @table_name;
        """
        return self.execute_query(query, params=[
            bigquery.ScalarQueryParameter("table_name", "STRING", table_id),
        ])

# --- MCP Tool Implementations ---
# These will be registered with FastMCP
_db: Optional[BigQueryDatabase] = None

def get_db():
    global _db
    if _db is None:
        raise RuntimeError("BigQueryDatabase not initialized")
    return _db

@mcp.tool()
async def execute_query(query: str) -> str:
    """Execute a SELECT query on the BigQuery database."""
    db = get_db()
    try:
        results = db.execute_query(query)
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
async def list_tables() -> str:
    """List all tables in the BigQuery database."""
    db = get_db()
    try:
        tables = db.list_tables()
        return json.dumps(tables, indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
async def describe_table(table_name: str) -> str:
    """Get the schema information for a specific table."""
    db = get_db()
    try:
        schema = db.describe_table(table_name)
        return json.dumps(schema, indent=2)
    except Exception as e:
        return f"Error: {e}"

# --- Health Check Endpoint ---
@app.get("/mcp")
async def mcp_status():
    return {"status": "ok", "message": "MCP BigQuery server is running.", "sse_endpoint": "/mcp/sse"}

# --- MCP SSE Transport Integration ---
# Create the SSE transport
sse = SseServerTransport("/mcp/messages")
# Mount the /mcp/messages POST handler
app.router.routes.append(Mount("/mcp/messages", app=sse.handle_post_message))
# Add the /mcp/sse GET handler
@app.get("/mcp/sse")
async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
        await mcp._mcp_server.run(
            read_stream,
            write_stream,
            mcp._mcp_server.create_initialization_options(),
        )

# --- Initialization Logic ---
def init_db_from_env():
    project = os.getenv("BQ_PROJECT_ID")
    location = os.getenv("BQ_LOCATION")
    key_file = os.getenv("BQ_KEY_FILE")
    datasets = os.getenv("BQ_DATASETS", "").split(",") if os.getenv("BQ_DATASETS") else []
    global _db
    _db = BigQueryDatabase(project, location, key_file, datasets)
    logger.info(f"BigQueryDatabase initialized from env: project={project}, location={location}, key_file={key_file}, datasets={datasets}")

def main():
    # Optionally parse CLI args or just use env vars
    import argparse
    parser = argparse.ArgumentParser(description='BigQuery MCP Server (FastMCP)')
    parser.add_argument('--project', help='BigQuery project', required=False)
    parser.add_argument('--location', help='BigQuery location', required=False)
    parser.add_argument('--key-file', help='BigQuery Service Account', required=False)
    parser.add_argument('--dataset', help='BigQuery dataset', required=False, action='append')
    parser.add_argument('--port', type=int, default=8080, help='Port for HTTP/SSE server (default: 8080)')
    args = parser.parse_args()
    # Set env vars for init_db_from_env
    if args.project:
        os.environ["BQ_PROJECT_ID"] = args.project
    if args.location:
        os.environ["BQ_LOCATION"] = args.location
    if args.key_file:
        os.environ["BQ_KEY_FILE"] = args.key_file
    if args.dataset:
        os.environ["BQ_DATASETS"] = ",".join(args.dataset)
    init_db_from_env()
    uvicorn.run(app, host="0.0.0.0", port=args.port)

if __name__ == "__main__":
    main()
