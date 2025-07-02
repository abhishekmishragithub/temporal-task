import sys
import os
import tempfile
import shutil
from git import Repo
import requests
from urllib.parse import urlparse
import argparse


def parse_issue_url(issue_url):
    """Parse GitHub issue URL to extract owner, repo, and issue number."""
    # url example: https://github.com/owner/repo/issues/123
    parsed = urlparse(issue_url)
    path_parts = parsed.path.strip('/').split('/')

    if len(path_parts) != 4 or path_parts[2] != 'issues':
        raise ValueError(f"Invalid GitHub issue URL: {issue_url}")
    owner = path_parts[0]
    repo = path_parts[1]
    issue_number = int(path_parts[3])
    return owner, repo, issue_number


def clone_repo(owner, repo, token):
    """Clone the repository to a temp directory."""
    temp_dir = tempfile.mkdtemp(prefix=f"pr-bot-{repo}-")
    print(f"Cloning repository to {temp_dir}...")
    clone_url = f"https://{token}@github.com/{owner}/{repo}.git"
    Repo.clone_from(clone_url, temp_dir)
    return temp_dir


def create_branch(repo_path, issue_number):
    """Create a new branch for the fix."""
    repo = Repo(repo_path)
    branch_name = f"fix-issue-{issue_number}"
    print(f"Creating branch {branch_name}...")
    repo.create_head(branch_name)
    repo.heads[branch_name].checkout()
    return branch_name


def apply_fix(repo_path, issue_number):
    """Apply a simple fix to the repository."""
    readme_path = os.path.join(repo_path, "README.md")
    print(f"Applying fix for issue #{issue_number}...")
    with open(readme_path, 'a') as f:
        f.write(f"\n\nFixed by PR Bot in response to issue #{issue_number}\n")
    return readme_path


def commit_and_push(repo_path, branch_name, issue_number):
    """Commit changes and push to remote."""
    repo = Repo(repo_path)
    print("Committing changes...")
    repo.index.add(["README.md"])
    repo.index.commit(f"Fix issue #{issue_number}")
    print(f"Pushing branch {branch_name}...")
    origin = repo.remote("origin")
    origin.push(branch_name)

    # simulate API rate limit after successful push but before PR creation
    print("Branch pushed successfully!")
    raise Exception("GitHub API rate limit exceeded!")


def create_pr(owner, repo, branch_name, issue_number, token):
    """Create a pull request on GitHub."""
    print(f"Creating pull request for issue #{issue_number}...")
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "title": f"Fix issue #{issue_number}",
        "body": f"This PR fixes issue #{issue_number}\n\nCloses #{issue_number}",
        "head": branch_name,
        "base": "main"
    }
    response = requests.post(url, json=data, headers=headers)
    response.raise_for_status()
    pr_data = response.json()
    return pr_data["html_url"]


def cleanup(repo_path):
    """Clean up the temp directory."""
    print(f"Cleaning up {repo_path}...")
    shutil.rmtree(repo_path)


def main():
    parser = argparse.ArgumentParser(description="Create a PR to fix a GitHub issue")
    parser.add_argument("issue_url", help="GitHub issue URL")
    parser.add_argument("token", help="GitHub Personal Access Token")

    args = parser.parse_args()

    try:
        # parse the issue URL
        owner, repo, issue_number = parse_issue_url(args.issue_url)
        print(f"Working on {owner}/{repo} issue #{issue_number}")

        # clone the repository
        repo_path = clone_repo(owner, repo, args.token)

        # create a new branch
        branch_name = create_branch(repo_path, issue_number)

        # apply the fix
        apply_fix(repo_path, issue_number)

        # commit and push (this will fail with rate limit error)
        commit_and_push(repo_path, branch_name, issue_number)

        # create pull request (never reached due to exception above)
        pr_url = create_pr(owner, repo, branch_name, issue_number, args.token)
        print(f"Pull request created: {pr_url}")

        # cleanup (never reached due to exception above)
        cleanup(repo_path)

    except Exception as e:
        print(f"Error: {e}")
        print("Script failed! Check for orphaned resources.")
        # note: cleanup is NOT called here, leaving the temp directory behind
        sys.exit(1)


if __name__ == "__main__":
    main()
