# Setup guide (browser only)

You need: this GitHub repo, a Google account (Gmail), and about 20 minutes.
You never install Python or open a terminal.

---

## Step 1 - Add your resume (3 min)

1. Open the repo on github.com, then open the `profile` folder.
2. Click **Add file -> Upload files**, drop your resume PDF, and name it **`resume.pdf`**. Click **Commit changes**.
3. Press **`.`** (the full-stop key) on the repo page. **github.dev** opens: VS Code in your browser.
4. Open `profile/resume.md`, delete the template text, paste your resume as plain text.
5. Open `config.yaml` and check `profile`, `search.terms` and `search.locations`.
6. Click the **Source Control** icon on the left (third icon), type a message such as "my resume", then click **Commit & Push**.

> github.dev has no server behind it, so it never times out. Unsaved edits stay in your browser until you commit.

## Step 2 - Free AI key (2 min)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in.
2. Click **Create API key** and copy it.

## Step 3 - Gmail App Password (3 min)

1. Turn on 2-Step Verification: [myaccount.google.com/signinoptions/twosv](https://myaccount.google.com/signinoptions/twosv).
2. Create an App Password: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Name it "Job agent" and copy the 16-character password.

Your normal Gmail password is never used or stored.

## Step 4 - Add secrets to GitHub (3 min)

Repo -> **Settings -> Secrets and variables -> Actions -> New repository secret**. Add three:

| Name | Value |
|---|---|
| `LLM_API_KEY` | the Gemini key from step 2 |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character App Password from step 3 |

## Step 5 - First agent run (5 min)

1. Repo -> **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Job agent -> Run workflow -> Run workflow**.
3. After 3-5 minutes it turns green. Click the run to see a summary (jobs found, scored, sent).

From now on it runs by itself every day at 09:00 India time.

## Step 6 - Review screen on Streamlit (5 min)

### 6a. Create a GitHub token for the app

1. Open [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new) (fine-grained token).
2. Name it "job agent app". Set the expiration to 1 year.
3. **Repository access -> Only select repositories ->** pick `job-application-agent`.
4. **Permissions -> Repository permissions:**
   - **Contents: Read and write** (to save your approvals)
   - **Actions: Read and write** (for the "Run now" and "Send approved now" buttons)
5. Click **Generate token** and copy it.

### 6b. Deploy

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub. Allow access to private repos when asked.
2. **Create app -> Yup, I have an app.**
   - Repository: `<your-username>/job-application-agent`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
3. **Advanced settings:** choose Python 3.12, then paste into **Secrets**:

   ```toml
   APP_PASSWORD = "pick-a-long-password"
   GH_REPO = "<your-username>/job-application-agent"
   GH_TOKEN = "github_pat_...the token from 6a..."
   ```

4. Click **Deploy**. Open the link, enter your `APP_PASSWORD`, and bookmark it (on your phone too).

The app comes from a private repo, so Streamlit keeps it private as well. The password is a second lock.

---

## Troubleshooting

| What you see | Fix |
|---|---|
| Run summary shows `ai: False` | `LLM_API_KEY` secret is missing or misspelled. |
| Reasons say "Scored offline (no AI key)" | Same as above. Or the free AI quota ran out; the keyword matcher took over for that run. |
| Everything is "Low match" | Paste your real resume into `profile/resume.md`, or lower `scoring.min_score`. Use **Low match -> Review** to rescue a job. |
| Status "Failed: Username and Password not accepted" | Recreate the Gmail App Password (2-Step Verification must be on). |
| "Approved" emails don't go out | Press **Send approved now**, or wait for the next daily run. Check the daily limit in `config.yaml`. |
| Streamlit shows "This app has gone to sleep" | Click **Yes, get this app back up!** Your data lives in GitHub, so nothing is lost. |
| "Could not start the workflow" in the app | Token is missing **Actions: Read and write**, or it has expired. |
| A job board returns 0 jobs | Sites sometimes block servers. The run continues with the other sites. |
| Try a different AI model | Change `llm.model` in `config.yaml` (any free model on the [Gemini pricing page](https://ai.google.dev/gemini-api/docs/pricing)). |

## Optional: see or edit the raw data

Switch the branch dropdown on github.com to **`data`** and open `data/jobs.json`. Each change is a commit, so you can see the full history of every application.
