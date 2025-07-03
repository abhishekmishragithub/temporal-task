import asyncio
import logging
import os
import sys

from activities import GitHubActivities
from shared import PR_BOT_TASK_QUEUE_NAME
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker
from workflows import GitHubPRWorkflow

# configure structured logging
IS_DEBUG = os.environ.get("DEBUG", "false").lower() in ["true", "1", "yes"]
logging.basicConfig(
    level=logging.DEBUG if IS_DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the Temporal worker for GitHub PR Bot."""

    # validate GitHub token
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        logger.error(
            "GitHub token not found",
            extra={
                "required_env_var": "GITHUB_TOKEN",
            },
        )
        sys.exit(1)

    # get Temporal server address
    temporal_address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")

    logger.info(
        "Starting GitHub PR Bot worker",
        extra={
            "temporal_address": temporal_address,
            "debug_mode": IS_DEBUG,
            "github_token_present": bool(github_token),
        },
    )

    try:
        # connect to Temporal server
        client = await Client.connect(
            temporal_address, data_converter=pydantic_data_converter
        )
        logger.info(
            "Connected to Temporal server",
            extra={
                "temporal_address": temporal_address,
            },
        )

        # create activities instance
        activities = GitHubActivities()

        # Create and configure worker
        worker = Worker(
            client,
            task_queue=PR_BOT_TASK_QUEUE_NAME,
            workflows=[GitHubPRWorkflow],
            activities=[
                activities.parse_issue_url,
                activities.get_issue_details,
                activities.clone_repo_and_create_branch,
                activities.generate_fix_with_ai,
                activities.apply_fix_and_commit,
                activities.push_changes,
                activities.create_pull_request,
                activities.cleanup_local_repo,
            ],
        )

        logger.info(
            "worker configured successfully",
            extra={
                "task_queue": PR_BOT_TASK_QUEUE_NAME,
                "workflow_count": 1,
                "activity_count": 6,
            },
        )

        logger.info(
            "worker started",
            extra={
                "temporal_address": temporal_address,
                "task_queue": PR_BOT_TASK_QUEUE_NAME,
                "listening": True,
                "hint": "Press Ctrl+C to stop",
            },
        )
        # run the worker
        await worker.run()

    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
        sys.exit(0)

    except Exception as e:
        logger.exception(
            "worker failed to start or run",
            extra={
                "temporal_address": temporal_address,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
