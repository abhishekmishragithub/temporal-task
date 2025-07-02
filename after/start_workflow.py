import asyncio
import argparse
import os
from temporalio.client import Client

from shared import BotInput
from workflows import GitHubPRWorkflow

async def main():
    """Start a GitHub PR Bot workflow."""
    parser = argparse.ArgumentParser(description="Start GitHub PR Bot workflow")
    parser.add_argument("issue_url", help="GitHub issue URL")
    args = parser.parse_args()

    if not os.environ.get("GITHUB_TOKEN"):
        print("ERROR: GITHUB_TOKEN environment variable not set")
        print("Please export GITHUB_TOKEN='your-github-pat'")
        return

    client = await Client.connect("localhost:7233")

    print(f"Starting workflow for issue: {args.issue_url}")

    handle = await client.start_workflow(
        GitHubPRWorkflow.run,
        BotInput(issue_url=args.issue_url),
        id=f"github-pr-bot-{args.issue_url.replace('/', '-')}",
        task_queue="github-pr-bot-queue",
    )

    print(f"Workflow started with ID: {handle.id}")
    print("Waiting for result...")

    result = await handle.result()

    print("✅ Workflow completed successfully!")
    print(f"📝 Pull request created: {result.pr_url}")


if __name__ == "__main__":
    asyncio.run(main())
