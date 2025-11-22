# Git & GitHub Setup Guide

Follow these steps to commit and push your code to GitHub.

## Step 1: Initialize Git Repository

```bash
cd /Users/ccarlson/workspace/clumio-bot
git init
```

## Step 2: Add All Files

```bash
git add .
```

## Step 3: Make Your First Commit

```bash
git commit -m "Initial commit: Clumio Bot API"
```

## Step 4: Create a GitHub Repository

1. Go to [github.com](https://github.com) and sign in
2. Click the **+** icon in the top right → **New repository**
3. Name your repository (e.g., `clumio-bot`)
4. Choose **Public** or **Private**
5. **DO NOT** initialize with README, .gitignore, or license (we already have files)
6. Click **Create repository**

## Step 5: Connect Local Repository to GitHub

After creating the repository, GitHub will show you commands. Use these:

```bash
# Add the remote repository (replace YOUR_USERNAME and REPO_NAME)
git remote add origin https://github.com/YOUR_USERNAME/REPO_NAME.git

# Or if using SSH:
# git remote add origin git@github.com:YOUR_USERNAME/REPO_NAME.git
```

## Step 6: Push to GitHub

```bash
# Rename branch to main (if needed)
git branch -M main

# Push your code
git push -u origin main
```

## Quick Reference Commands

```bash
# Check status
git status

# See what files are staged
git diff --cached

# Add specific file
git add filename.py

# Commit changes
git commit -m "Your commit message"

# Push changes
git push

# Pull latest changes
git pull
```

## Important Notes

- **Never commit sensitive data** like API tokens or passwords
- The `.gitignore` file will automatically exclude:
  - Environment files (`.env`)
  - Python cache files (`__pycache__/`)
  - Virtual environments (`venv/`, `env/`)
  - IDE files (`.vscode/`, `.idea/`)

