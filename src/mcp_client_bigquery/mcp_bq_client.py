import requests
import json

try:
    from sseclient import SSEClient
except ImportError:
    SSEClient = None

SERVER_URL = "http://localhost:8080"  # Change if needed

def list_tables():
    resp = requests.get(f"{SERVER_URL}/list-tables")
    print(json.dumps(resp.json(), indent=2))

def describe_table(table_name):
    resp = requests.post(f"{SERVER_URL}/describe-table", json={"table_name": table_name})
    print(json.dumps(resp.json(), indent=2))

def execute_query(query):
    resp = requests.post(f"{SERVER_URL}/execute-query", json={"query": query})
    print(json.dumps(resp.json(), indent=2))

def execute_query_sse(query):
    if SSEClient is None:
        print("Please install sseclient: pip install sseclient")
        return
    resp = requests.post(f"{SERVER_URL}/execute-query-sse", json={"query": query}, stream=True)
    client = SSEClient(resp)
    for event in client.events():
        print(event.data)

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="MCP BigQuery Client")
    parser.add_argument("--list-tables", action="store_true", help="List all tables")
    parser.add_argument("--describe-table", help="Describe a table (format: dataset.table)")
    parser.add_argument("--execute-query", help="Execute a SQL query")
    parser.add_argument("--execute-query-sse", help="Execute a SQL query with SSE streaming")
    parser.add_argument("--server-url", default=SERVER_URL, help="Server URL (default: http://localhost:8080)")

    args = parser.parse_args()
    SERVER_URL = args.server_url

    if args.list_tables:
        list_tables()
    elif args.describe_table:
        describe_table(args.describe_table)
    elif args.execute_query:
        execute_query(args.execute_query)
    elif args.execute_query_sse:
        execute_query_sse(args.execute_query_sse)
    else:
        parser.print_help() 