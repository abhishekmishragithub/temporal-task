import asyncio
import logging
import os
import sys
from typing import Optional

from activities import GitHubActivities
from shared import PR_BOT_TASK_QUEUE_NAME
from start_workflow import parse_github_url
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.events import MouseEvent
from textual.logging import TextualHandler
from textual.widgets import Button, Footer, Header, Input, Link, RichLog, Static
from workflows import GitHubPRWorkflow

logging.basicConfig(level=logging.INFO, handlers=[TextualHandler()])
logger = logging.getLogger(__name__)

if not all(
    [
        os.environ.get("GITHUB_TOKEN"),
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
    ]
):
    print(
        "Error: GITHUB_TOKEN and (GEMINI_API_KEY or GOOGLE_API_KEY) must be set in the environment.",
        file=sys.stderr,
    )
    sys.exit(1)


class NoMouseInput(Input):  # type: ignore[misc]
    """An Input widget that ignores mouse events to prevent garbled text."""

    def on_mouse_event(self, event: MouseEvent) -> None:
        event.stop()


class PRBotApp(App):  # type: ignore[misc]
    """An AI-powered GitHub PR Bot dashboard."""

    CSS_PATH = "app.css"
    BINDINGS = [("c", "clear_log", "Clear Log"), ("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.client: Client | None = None
        self.worker_task: Optional[asyncio.Task[None]] = None
        self.activities = GitHubActivities()
        self.temporal_status_widget = Static("Disconnected", id="temporal-status-value")
        self.worker_status_widget = Static("Stopped", id="worker-status-value")

    def compose(self) -> ComposeResult:
        """Create the layout for the app."""
        yield Header()
        with Container(id="main-container"):
            with Container(id="left-pane"):
                yield Static("🤖 AI GitHub PR Bot", id="title")
                yield Static(
                    "Paste a GitHub issue URL and click 'Fix Issue' to start.",
                    id="subtitle",
                )
                yield NoMouseInput(
                    placeholder="https://github.com/owner/repo/issues/123",
                    id="url-input",
                )
                # yield Input(placeholder="https://github.com/owner/repo/issues/123", id="url-input")
                yield Button("Fix Issue", variant="success", id="start-button")

                with Container(id="status-grid"):
                    yield Static("Temporal:")
                    yield self.temporal_status_widget
                    yield Static("Worker:")
                    yield self.worker_status_widget

                yield Link(
                    "View Temporal UI",
                    url="http://localhost:8080",
                    id="ui-link",
                )

            with VerticalScroll(id="right-pane"):
                yield RichLog(id="log", auto_scroll=True, markup=True)
        yield Footer()

    async def on_mount(self) -> None:
        """Called when the app is mounted. Connect to Temporal and start the worker."""
        self.query_one("#url-input", Input).focus()
        await self._connect_and_start_worker()

    async def _connect_and_start_worker(self) -> None:
        """Connects to the Temporal server and starts the worker in a background task."""
        log = self.query_one(RichLog)
        temporal_address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
        try:
            log.write(f"Connecting to Temporal at {temporal_address}...")
            self.client = await Client.connect(
                temporal_address, data_converter=pydantic_data_converter
            )
            self.temporal_status_widget.update(f"Connected to {self.client.namespace}")
            log.write(
                f"[bold green] ✅ Temporal client connected to namespace '{self.client.namespace}'[/bold green]"
            )

            worker = Worker(
                self.client,
                task_queue=PR_BOT_TASK_QUEUE_NAME,
                workflows=[GitHubPRWorkflow],
                activities=[
                    self.activities.parse_issue_url,
                    self.activities.get_issue_details,
                    self.activities.clone_repo_and_create_branch,
                    self.activities.generate_fix_with_ai,
                    self.activities.apply_fix_and_commit,
                    self.activities.push_changes,
                    self.activities.create_pull_request,
                    self.activities.cleanup_local_repo,
                ],
            )
            self.worker_task = asyncio.create_task(worker.run())
            self.worker_status_widget.update("Running")
            log.write(
                f"[bold green]✓ Worker started successfully on task queue '{PR_BOT_TASK_QUEUE_NAME}'[/bold green]"
            )
        except Exception as e:
            self.temporal_status_widget.update("Connection Failed")
            self.worker_status_widget.update("Failed")
            log.write(f"[bold red]✗ Failed to connect or start worker: {e}[/bold red]")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the 'Fix Issue' button press."""
        if event.button.id == "start-button":
            # url_input = self.query_one("#url-input", Input)
            url_input = self.query_one("#url-input", NoMouseInput)
            issue_url = url_input.value
            log = self.query_one(RichLog)

            if not self.client or self.worker_status_widget.renderable != "Running":
                log.write(
                    "[bold red]✗ Cannot start workflow: Worker is not running.[/bold red]"
                )
                return

            try:
                request = parse_github_url(issue_url)
            except ValueError as e:
                log.write(f"[bold red]✗ Invalid URL: {e}[/bold red]")
                return

            log.write("-" * 50)
            log.write(f"▶️ Starting workflow '{request.workflow_id}'...")
            log.write(f"  Repo: {request.repo_path}")
            log.write(f"  Issue: {request.issue_number}")

            url_input.value = ""
            url_input.disabled = True
            event.button.disabled = True

            try:
                handle = await self.client.start_workflow(
                    GitHubPRWorkflow.run,
                    request,
                    id=request.workflow_id,
                    task_queue=PR_BOT_TASK_QUEUE_NAME,
                )
                result = await handle.result()
                log.write(
                    f"\n[bold green]✓ Workflow '{handle.id}' completed![/bold green]"
                )
                log.write(
                    f"  PR Created: [link={result.pull_request.url}]{result.pull_request.url}[/link]"
                )
            except Exception as e:
                log.write(
                    f"\n[bold red]✗ Workflow '{request.workflow_id}' failed![/bold red]"
                )
                log.write(f"  Error: {e}")
            finally:
                url_input.disabled = False
                event.button.disabled = False
                url_input.focus()

    def action_clear_log(self) -> None:
        """An action to clear the log."""
        self.query_one(RichLog).clear()


if __name__ == "__main__":
    app = PRBotApp()
    app.run()
