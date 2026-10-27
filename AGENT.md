# Lab GPU: instructions for Claude agents

Your user (a Sripada Lab member) has a personal GPU machine in the lab's AWS
account: Ubuntu 22.04, one NVIDIA Tesla T4 (16 GB), 4 vCPUs, 16 GB RAM, a
120 GB disk, Python 3.10 and CUDA drivers. It sleeps when unused. You wake it
with `labgpu up` and run commands on it over SSH (`ssh labgpu ...`). Your user
is probably not technical, so do the technical work yourself and explain
things in plain words.

## 1. Install (once)

Run this on the user's computer (Mac or Linux). It needs no admin password and
installs everything into `~/.labgpu`:

```bash
curl -fsSL https://raw.githubusercontent.com/SripadaLab/labgpu-client/main/install.sh | bash
```

- If it prints `STOP: Python 3 isn't installed`, a macOS window has opened.
  Ask the user to click **Install**, wait for it to finish (about 5 minutes),
  then run the installer again.
- Running it again later is safe and updates labgpu.
- The command is `~/.labgpu/bin/labgpu`. It's on the PATH in new terminals;
  use the full path if `labgpu` isn't found.

## 2. Sign in (the user does this part in the browser)

Run `~/.labgpu/bin/labgpu login`. It opens a sign-in page in the browser and
waits up to 15 minutes. **Before running it**, tell the user what's coming, in
words like these:

> A sign-in page is opening in your web browser. **First time:** find the email
> titled "Your Sripada Lab GPU account" (check spam), copy the temporary password
> into the page, choose your own password, then scan the QR code with an
> authenticator app on your phone (Okta Verify or Duo Mobile) and type the 6-digit code it
> shows. **After that:** just your lab GPU password and the 6-digit code from the
> app. Tell me when the page says "You're signed in".

- Never ask the user to type their password or code into the chat, and never
  try to fill the page yourself.
- A sign-in lasts 12 hours. If any labgpu command exits with **code 3** ("not
  signed in" or "sign-in expired"), run `labgpu login` again with the same
  explanation (no email step this time).
- If the page didn't open, give the user the link that `labgpu login` printed.
- If `labgpu login` itself fails with an error (not a wrong password), run the
  installer again (step 1) to update labgpu, then sign in again.
- Forgotten password, a lost phone, or "temporary password has expired": ask
  the lab admin (Aman Taxali, ataxali@umich.edu).

## 3. Use the machine

```bash
~/.labgpu/bin/labgpu status   # asleep or awake, minutes left, this month's cost
~/.labgpu/bin/labgpu up       # wake it; waits until ready (about 2 minutes)
ssh labgpu 'nvidia-smi'       # run commands on it
~/.labgpu/bin/labgpu down     # put it to sleep when finished
```

- **Files:** use `rsync -av project/ labgpu:project/` and
  `scp labgpu:results.csv .`.
- **Python:** create a virtual environment on the machine, for example
  `ssh labgpu 'python3 -m venv ~/env && ~/env/bin/pip install torch transformers'`.
  Everything on the disk stays there between wakes. If `python3 -m venv` fails
  with "ensurepip is not available", install it first:
  `ssh labgpu 'sudo apt-get install -y python3.10-venv'`. The `ubuntu` user can
  use sudo without a password, so you can install any system packages you need.
- **Hugging Face:** gated models (Gemma, Llama, ...) need the user's own
  token, and they also need to accept each model's terms on its
  huggingface.co page.
  - With the machine awake, run `~/.labgpu/bin/labgpu hf-token`. It opens a
    browser page where the user pastes the token, and saves it on the machine
    in `~/.cache/huggingface/token`, where Hugging Face libraries find it.
  - First tell them: "A page is opening. On huggingface.co go to Settings →
    Access Tokens, create a token with Read access, and paste it into the page."
  - Don't take the token through the chat.
- **Anything over a few minutes:** run it in tmux so it survives a dropped
  connection, then check on it.
  - Start: `ssh labgpu 'tmux new -d -s job "cd ~/project && python run.py > run.log 2>&1"'`
  - Check: `ssh labgpu 'tail -5 ~/project/run.log'`
- **Web apps and playgrounds:** start the app on the machine on port 8000, then
  run `ssh -N -L 8000:localhost:8000 labgpu` in the background on the user's
  computer and open http://localhost:8000.
- **When sessions end:** each session ends 2 hours after `labgpu up`. The
  machine also stops after 30 minutes with no SSH connection and an idle GPU
  and CPU. Write results to files as you go, plan longer work in chunks of
  under 2 hours, and run `labgpu up` again to continue. Nothing on the disk is
  lost.
- **Model size:** the T4 has 16 GB of GPU memory. Models up to about 3-4B
  parameters fit comfortably in 16-bit. 7-8B models need 8-bit or 4-bit
  quantization. Larger models won't fit; tell the user to ask the lab admin.
- **Precision and speed:** the T4 is a Turing GPU, the same generation as the
  lab server. It has no fast bfloat16 and no FlashAttention 2, so use float16,
  or float32 to match the lab server's results exactly.

## 4. Rules

- **No participant data, PHI or other regulated research data** on this machine,
  ever. Public models, synthetic prompts and published datasets only. If a task
  needs participant data, stop and tell the user to ask the lab admin.
- **Cost:** about $0.53 per hour while awake, with a $100 per month limit per
  person. Check it with `labgpu status`. When the limit is reached, the
  machine can't start until the 1st of next month. Run `labgpu down` when
  you're done instead of waiting for the idle timeout.
- Don't share the machine or its access with anyone else.
