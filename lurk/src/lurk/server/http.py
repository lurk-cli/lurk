"""HTTP API server — serves context via REST at localhost:4141."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from ..context.model import ContextModel
from ..enrichment.pipeline import EnrichmentPipeline
from ..llm.config import load_llm_config
from ..llm.enhanced_prompt import generate_enhanced_prompt
from ..llm.provider import LLMProvider, create_provider
from ..server.prompt import generate_prompt
from ..store.database import ensure_schema, get_connection

logger = logging.getLogger("lurk.http")


class ContextServer:
    """HTTP server that serves the context model and runs enrichment in the background."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4141) -> None:
        self.host = host
        self.port = port
        self.model = ContextModel()
        self.pipeline = EnrichmentPipeline()
        self._stop_event = threading.Event()
        # Initialize LLM provider (optional)
        llm_config = load_llm_config()
        self.llm_provider: LLMProvider | None = create_provider(llm_config)
        if self.llm_provider:
            logger.info("LLM provider: %s (%s)", self.llm_provider.name, llm_config.model)

    def start(self) -> None:
        """Start the HTTP server and background enrichment."""
        # Initialize DB schema
        conn = get_connection()
        try:
            ensure_schema(conn)
            self.model.load_from_db(conn)
        finally:
            conn.close()

        # Start enrichment thread
        enrichment_thread = threading.Thread(
            target=self._enrichment_loop, daemon=True, name="enrichment"
        )
        enrichment_thread.start()

        # Start HTTP server
        self._run_http()

    def _enrichment_loop(self) -> None:
        """Background loop that enriches events and updates the model."""
        logger.info("Enrichment loop started")
        while not self._stop_event.is_set():
            try:
                count = self.pipeline.run_once()
                if count > 0:
                    # Reload recent enriched events into model
                    conn = get_connection()
                    try:
                        from ..store.database import fetch_recent_enriched
                        recent = fetch_recent_enriched(conn, hours=0.01, limit=50)
                        for event in reversed(recent):
                            self.model.process_enriched_event(event)
                    finally:
                        conn.close()
            except Exception:
                logger.exception("Error in enrichment loop")
            self._stop_event.wait(timeout=3.0)

    def _run_http(self) -> None:
        """Run the HTTP server."""
        try:
            from starlette.applications import Starlette
            from starlette.responses import JSONResponse, PlainTextResponse
            from starlette.routing import Route
            import uvicorn
        except ImportError:
            # Fallback to a simple HTTP server
            self._run_simple_http()
            return

        # CORS middleware for browser extension access
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware

        async def status(request):
            return JSONResponse({
                "status": "ok",
                "version": "0.1.0",
                "daemon": True,
            })

        async def context_now(request):
            return JSONResponse(self.model.now.to_dict())

        async def context_session(request):
            return JSONResponse(self.model.session.to_dict())

        async def context_prompt(request):
            max_tokens = int(request.query_params.get("max_tokens", 250))
            tool = request.query_params.get("for", "coding")
            text = generate_enhanced_prompt(self.model, self.llm_provider, max_tokens=max_tokens, tool=tool)
            return PlainTextResponse(text)

        async def context_project(request):
            name = request.path_params.get("name", "")
            if not name:
                name = self.model.now.project or ""
            return JSONResponse(self.model.projects.get(name, {}))

        async def context_full(request):
            return JSONResponse(self.model.to_dict())

        async def agents_status(request):
            return JSONResponse(self.model.agents.to_dict())

        async def agents_attention(request):
            return JSONResponse([item.to_dict() for item in self.model.agents.get_attention_queue()])

        async def agents_handoff(request):
            from_id = request.query_params.get("from", "")
            to_tool = request.query_params.get("to", "")
            if not from_id:
                return JSONResponse({"error": "Provide 'from' query param (session_id)"}, status_code=400)
            return JSONResponse(self.model.agents.get_handoff_context(from_id, to_tool))

        async def agents_workflow(request):
            return JSONResponse(self.model.agents.get_workflow_summary())

        app = Starlette(
            routes=[
                Route("/status", status),
                Route("/context/now", context_now),
                Route("/context/session", context_session),
                Route("/context/prompt", context_prompt),
                Route("/context/project/{name:str}", context_project),
                Route("/context", context_full),
                Route("/agents", agents_status),
                Route("/agents/attention", agents_attention),
                Route("/agents/handoff", agents_handoff),
                Route("/agents/workflow", agents_workflow),
                Route("/", lambda r: PlainTextResponse("lurk context broker v0.1.0")),
            ],
            middleware=[
                Middleware(
                    CORSMiddleware,
                    allow_origins=["*"],
                    allow_methods=["GET"],
                    allow_headers=["*"],
                ),
            ],
        )

        logger.info("HTTP API starting at http://%s:%d", self.host, self.port)
        uvicorn.run(app, host=self.host, port=self.port, log_level="warning")

    def _run_simple_http(self) -> None:
        """Fallback HTTP server using stdlib."""
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import urllib.parse

        model = self.model
        llm_provider = self.llm_provider

        class Handler(BaseHTTPRequestHandler):
            def _cors_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors_headers()
                self.end_headers()

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                params = urllib.parse.parse_qs(parsed.query)

                if path == "/status":
                    self._json_response({"status": "ok", "version": "0.1.0", "daemon": True})
                elif path == "/context/now":
                    self._json_response(model.now.to_dict())
                elif path == "/context/session":
                    self._json_response(model.session.to_dict())
                elif path == "/context/prompt":
                    max_tokens = int(params.get("max_tokens", [250])[0])
                    tool = params.get("for", ["coding"])[0]
                    text = generate_enhanced_prompt(model, llm_provider, max_tokens=max_tokens, tool=tool)
                    self._text_response(text)
                elif path.startswith("/context/project/"):
                    name = path.split("/context/project/", 1)[-1]
                    self._json_response(model.projects.get(name, {}))
                elif path == "/context":
                    self._json_response(model.to_dict())
                elif path == "/agents":
                    self._json_response(model.agents.to_dict())
                elif path == "/agents/attention":
                    self._json_response([item.to_dict() for item in model.agents.get_attention_queue()])
                elif path == "/agents/handoff":
                    from_id = params.get("from", [""])[0]
                    to_tool = params.get("to", [""])[0]
                    self._json_response(model.agents.get_handoff_context(from_id, to_tool))
                elif path == "/agents/workflow":
                    self._json_response(model.agents.get_workflow_summary())
                elif path == "/":
                    self._text_response("lurk context broker v0.1.0")
                else:
                    self.send_error(404)

            def _json_response(self, data):
                body = json.dumps(data, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def _text_response(self, text):
                body = text.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass  # Suppress request logs

        server = HTTPServer((self.host, self.port), Handler)
        logger.info("HTTP API starting at http://%s:%d (stdlib fallback)", self.host, self.port)
        print(f"[lurk] Context API ready at http://{self.host}:{self.port}")
        server.serve_forever()
