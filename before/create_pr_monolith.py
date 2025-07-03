import argparse
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from git import Repo

IS_DEBUG = os.environ.get("DEBUG", "false").lower() in ["true", "1", "yes"]
logging.basicConfig(
    level=logging.DEBUG if IS_DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def parse_issue_url(issue_url: str) -> tuple[str, str, int]:
    """Parse GitHub issue URL to extract owner, repo, and issue number."""
    logger.info(
        "Parsing GitHub issue URL",
        extra={
            "issue_url": issue_url,
        },
    )

    # url example: https://github.com/owner/repo/issues/123
    parsed = urlparse(issue_url)
    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) != 4 or path_parts[2] != "issues":
        logger.error(
            "Invalid GitHub issue URL format",
            extra={
                "issue_url": issue_url,
                "path_parts": path_parts,
            },
        )
        raise ValueError(f"Invalid GitHub issue URL: {issue_url}")

    owner, repo, _, issue_number_str = path_parts
    issue_number = int(issue_number_str)
    # owner = path_parts[0]
    # repo = path_parts[1]
    # issue_number = int(path_parts[3])

    logger.info(
        "Successfully parsed issue URL",
        extra={
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
        },
    )

    return owner, repo, issue_number


def clone_repo(owner: str, repo_name: str, token: str) -> Path:
    """Clone the repository to a temp directory."""
    temp_dir = Path(tempfile.mkdtemp(prefix=f"pr-bot-{repo_name}-"))

    logger.info(
        "Starting repository clone",
        extra={
            "owner": owner,
            "repo": repo_name,
            "temp_directory": str(temp_dir),
        },
    )

    clone_url = f"https://{token}@github.com/{owner}/{repo_name}.git"
    Repo.clone_from(clone_url, str(temp_dir))

    logger.info(
        "Repository cloned successfully",
        extra={
            "owner": owner,
            "repo": repo_name,
            "local_path": temp_dir,
        },
    )

    return temp_dir


def create_branch(repo_path: Path, issue_number: int) -> str:
    """Create a new branch for the fix."""
    repo = Repo(str(repo_path))
    branch_name = f"fix-issue-{issue_number}"

    logger.info(
        "Creating branch",
        extra={
            "branch_name": branch_name,
            "issue_number": issue_number,
            "repo_path": str(repo_path),
        },
    )

    repo.create_head(branch_name).checkout()
    # repo.heads[branch_name].checkout()

    logger.info(
        "Branch created and checked out",
        extra={
            "branch_name": branch_name,
        },
    )

    return branch_name


def apply_fix(repo_path: Path, issue_number: int) -> None:
    """Apply a simple fix to the repository."""
    readme_path = repo_path / "README.md"

    logger.info(
        "Applying fix for issue",
        extra={
            "issue_number": issue_number,
            "readme_path": str(readme_path),
        },
    )

    with readme_path.open("a", encoding="utf-8") as f:
        f.write(f"\n\nFixed by PR Bot in response to issue #{issue_number}\n")

    logger.info(
        "Fix applied successfully",
        extra={
            "issue_number": issue_number,
        },
    )

    # return readme_path


def commit_and_push(repo_path: Path, branch_name: str, issue_number: int) -> None:
    """Commit changes and push to remote."""
    repo = Repo(str(repo_path))

    logger.info(
        "Committing changes",
        extra={
            "branch_name": branch_name,
            "issue_number": issue_number,
        },
    )

    repo.index.add(["README.md"])
    commit = repo.index.commit(f"Fix issue #{issue_number}")

    logger.info(
        "Changes committed successfully",
        extra={
            "commit_hash": commit.hexsha,
            "branch_name": branch_name,
        },
    )

    logger.info(
        "Pushing branch to remote",
        extra={
            "branch_name": branch_name,
        },
    )

    origin = repo.remote("origin")
    origin.push(branch_name)

    logger.info(
        "Branch pushed successfully",
        extra={
            "branch_name": branch_name,
        },
    )

    # simulate API rate limit after successful push but before PR creation
    logger.error(
        "Simulating GitHub API rate limit error",
        extra={
            "error_type": "rate_limit",
            "branch_name": branch_name,
        },
    )
    raise Exception("GitHub API rate limit exceeded!")


def create_pr(
    owner: str, repo_name: str, branch_name: str, issue_number: int, token: str
) -> str:
    """Create a pull request on GitHub."""
    logger.info(
        "Creating pull request",
        extra={
            "owner": owner,
            "repo": repo_name,
            "branch_name": branch_name,
            "issue_number": issue_number,
        },
    )

    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    data = {
        "title": f"Fix issue #{issue_number}",
        "body": f"This PR fixes issue #{issue_number}\n\nCloses #{issue_number}",
        "head": branch_name,
        "base": "main",
    }

    response = requests.post(url, json=data, headers=headers, timeout=30)
    response.raise_for_status()
    pr_data = response.json()
    pr_url = str(pr_data["html_url"])

    logger.info(
        "Pull request created successfully",
        extra={
            "pr_url": pr_url,
            "pr_number": pr_data.get("number"),
            "issue_number": issue_number,
        },
    )

    return pr_url


def cleanup(repo_path: Path) -> None:
    """Clean up the temp directory."""
    logger.info(
        "Starting cleanup",
        extra={
            "repo_path": str(repo_path),
        },
    )

    if repo_path.exists():
        shutil.rmtree(repo_path)
        logger.info(
            "Cleanup completed successfully", extra={"cleaned_path": str(repo_path)}
        )
    else:
        logger.warning(
            "Cleanup path does not exist", extra={"path_to_clean": str(repo_path)}
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a PR to fix a GitHub issue")
    parser.add_argument("issue_url", help="GitHub issue URL")

    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        logger.error("GITHUB_TOKEN environment variable not set")
        sys.exit(1)

    logger.info(
        "Starting GitHub PR Bot (monolith version)",
        extra={
            "issue_url": args.issue_url,
            "debug_mode": IS_DEBUG,
        },
    )

    repo_path: Path | None = None
    repo_name: str = "unknown"

    try:
        # parse the issue URL
        owner, repo, issue_number = parse_issue_url(args.issue_url)

        logger.info(
            "Starting PR creation workflow",
            extra={
                "owner": owner,
                "repo": repo,
                "issue_number": issue_number,
            },
        )

        # clone the repository
        repo_path = clone_repo(owner, repo, token)

        # create a new branch
        branch_name = create_branch(repo_path, issue_number)

        # apply the fix
        apply_fix(repo_path, issue_number)

        # commit and push (this will fail with rate limit error)
        commit_and_push(repo_path, branch_name, issue_number)

        # create pull request (never reached due to exception above)
        pr_url = create_pr(owner, repo, branch_name, issue_number, token)

        logger.info(
            "Workflow completed successfully",
            extra={
                "pr_url": pr_url,
                "issue_number": issue_number,
            },
        )

        # cleanup (never reached due to exception above)
        cleanup(repo_path)

    except Exception as e:
        logger.error(
            "Script execution failed",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        logger.warning(
            "Script failed - check for orphaned resources",
            extra={
                "temp_directory_prefix": f"pr-bot-{repo_name}-",
            },
        )
        # note: cleanup is NOT called here, leaving the temp directory behind
        sys.exit(1)


if __name__ == "__main__":
    main()
