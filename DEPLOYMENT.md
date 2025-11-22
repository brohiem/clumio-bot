# Deploying to Vercel

This guide explains how to deploy the Clumio Bot to Vercel.

## Prerequisites

- A Vercel account (sign up at [vercel.com](https://vercel.com))
- Vercel CLI installed (optional, for CLI deployment)

## Method 1: Deploy via Vercel CLI (Recommended)

1. **Install Vercel CLI** (if not already installed):
   ```bash
   npm install -g vercel
   ```

2. **Login to Vercel**:
   ```bash
   vercel login
   ```

3. **Navigate to your project directory**:
   ```bash
   cd /Users/ccarlson/workspace/clumio-bot
   ```

4. **Deploy to Vercel**:
   ```bash
   vercel
   ```
   
   Follow the prompts:
   - Set up and deploy? **Yes**
   - Which scope? (select your account)
   - Link to existing project? **No** (for first deployment)
   - Project name? (press Enter for default or enter a custom name)
   - Directory? (press Enter for current directory)

5. **Set Environment Variables**:
   After deployment, set your environment variables:
   ```bash
   vercel env add CLUMIO_API_TOKEN
   vercel env add CLUMIO_API_BASE_URL
   ```
   
   Or set them via the Vercel dashboard (see Method 2, Step 4)

6. **Redeploy** (to apply environment variables):
   ```bash
   vercel --prod
   ```

## Method 2: Deploy via GitHub Integration

1. **Push your code to GitHub**:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin <your-github-repo-url>
   git push -u origin main
   ```

2. **Import Project in Vercel**:
   - Go to [vercel.com/dashboard](https://vercel.com/dashboard)
   - Click **Add New** → **Project**
   - Import your GitHub repository
   - Vercel will auto-detect it's a Python project

3. **Configure Project**:
   - Framework Preset: **Other**
   - Root Directory: `./` (or leave as default)
   - Build Command: (leave empty, Vercel handles Python automatically)
   - Output Directory: (leave empty)

4. **Set Environment Variables**:
   - In the project settings, go to **Environment Variables**
   - Add the following:
     - `CLUMIO_API_TOKEN` = `your_api_token_here`
     - `CLUMIO_API_BASE_URL` = `https://us-west-2.api.clumio.com`
   - Make sure to set them for **Production**, **Preview**, and **Development** environments

5. **Deploy**:
   - Click **Deploy**
   - Vercel will build and deploy your app

## Method 3: Deploy via Vercel Dashboard

1. **Install Vercel CLI**:
   ```bash
   npm install -g vercel
   ```

2. **Deploy from current directory**:
   ```bash
   cd /Users/ccarlson/workspace/clumio-bot
   vercel
   ```

3. **Set environment variables in dashboard**:
   - Go to your project in [vercel.com/dashboard](https://vercel.com/dashboard)
   - Navigate to **Settings** → **Environment Variables**
   - Add:
     - `CLUMIO_API_TOKEN`
     - `CLUMIO_API_BASE_URL`

4. **Redeploy** to apply environment variables

## Environment Variables

Make sure to set these in Vercel:

- **CLUMIO_API_TOKEN**: Your Clumio API token
- **CLUMIO_API_BASE_URL**: Clumio API base URL (e.g., `https://us-west-2.api.clumio.com`)

## Testing Your Deployment

Once deployed, you can test your endpoints:

- Health check: `https://your-project.vercel.app/health`
- Inventory: `https://your-project.vercel.app/inventory?type=s3`
- Restore: `https://your-project.vercel.app/restore?type=s3&bucket-name=your-bucket`

## Troubleshooting

- **Build errors**: Check that `requirements.txt` includes all dependencies
- **Environment variables not working**: Make sure to redeploy after adding environment variables
- **404 errors**: Verify your `vercel.json` routes configuration is correct

