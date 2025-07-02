# 🐙🤖 GitHub PR Bot (Powered by Temporal)

![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)
![Temporal](https://img.shields.io/badge/Temporal-Workflow-lightgrey?logo=temporal)
![License](https://img.shields.io/github/license/abhishekmishragithub/temporal-task)

Automate branch creation ➜ commits ➜ pushes ➜ **Pull Requests**;  and guarantee cleanup even when GitHub rate limits or your network flakes out.
Temporal handles retries & compensation so you don’t have to babysit brittle shell scripts.

---

## ❗ Problem

Classic one-shot scripts look like this:

1. Clone repo into `/tmp`
2. Create branch
3. Commit changes
4. Push branch
5. Open PR
6. Delete `/tmp` directory

If step 4 fails (e.g due to rate limiting) you’re left with orphaned temp dirs **and** half-done state on GitHub. Re-running just makes more mess.

---

## 🛠️ Solution (Temporal FTW)

Temporal workflows:

- **Retry** failed activities with backoff  
- **Guarantee** that “finally” cleanup runs  
- **Record** every attempt ( and is searchable in UI)  

The workflow retries `push_changes` twice, then succeeds on the 3rd try, and _always_ deletes the temp repo directory afterward.

---

## Setup Instructions

### Prerequisites

- Python 3.8 or higher
- Docker and Docker Compose
- Git
- GitHub account with a Personal Access Token

### 1. Create a Target Repository

1. Go to GitHub and create a new public repository
2. Add a `README.md` file with some initial content
3. Create an issue in the repository (e.g., "Add bot signature to README")
4. Note the issue URL (e.g., `https://github.com/yourusername/test-repo/issues/1`)

### 2. Create GitHub Personal Access Token

1. Go to GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Give it a descriptive name (e.g., "PR Bot Demo")
4. Select the `repo` scope (full control of private repositories)
5. Click "Generate token"
6. Copy the token immediately (it won't be shown again)

### 3. Set Up Python Environment

```bash
# clone this repository
git clone https://github.com/abhishekmishragithub/temporal-task.git
cd temporal-task

# create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# install dependencies
pip install -r requirements.txt
```

### 4. Start Temporal Server

```bash
# start Temporal using docker compose
docker-compose up -d

# verify Temporal is running
# Open http://localhost:8080 in your browser
```

### 5. Set Environment Variable

```bash
export GITHUB_TOKEN='ghp_your_token_here'
```

## Running the Demo

### Running the "Before" Version (monolithic Script)

This demonstrates the brittle nature of traditional scripts:

```bash
python before/create_pr_monolith.py https://github.com/yourusername/test-repo/issues/1 $GITHUB_TOKEN
```

**Expected behavior:**
- The script will clone the repo and push a branch
- It will then fail with "GitHub API rate limit exceeded!"
- Check your temp directory (`/tmp` on Linux/Mac) - you'll find orphaned `pr-bot-*` directories
- The pushed branch exists on GitHub but no PR was created

### Running the "After" Version (Temporalized)

This demonstrates the resilient, self-healing nature of Temporal workflows:

**Terminal 1 - Start the Worker:**
```bash
export GITHUB_TOKEN='ghp_your_token_here'
python after/run_worker.py
```

**Terminal 2 - Start the Workflow:**
```bash
export GITHUB_TOKEN='ghp_your_token_here'
python after/start_workflow.py https://github.com/yourusername/test-repo/issues/1
```

### Expected "After" Behavior

1. **Watch the worker logs** to see:
   - The `push_changes` activity failing on attempts 1 and 2 (simulated failures)
   - Automatic retries with exponential backoff
   - Success on attempt 3
   - Cleanup activity running in the `finally` block

2. **Check the Temporal UI** at [http://localhost:8080](http://localhost:8080):
   - Navigate to the default namespace
   - Find your workflow (ID starts with `github-pr-bot-`)
   - Click on it to see the full execution history
   - Observe:
     - Input/output for each activity
     - Retry attempts for `push_changes`
     - The `cleanup_local_repo` activity in the Event History (always runs)

3. **Verify cleanup worked**:
   - Check your temp directory - no orphaned `pr-bot-*` directories
   - Even if the workflow failed, cleanup would have run

4. **Check GitHub**:
   - A new PR should be created that references the issue
   - The PR description includes "Closes #1" to auto-close the issue when merged
