# Lab GPU: instructions for Claude agents

Your user (a Sripada Lab member) has a personal GPU machine in the lab's AWS
account: Ubuntu 22.04, one NVIDIA GPU, 4 vCPUs, 16-32 GB RAM, a disk of 120 GB
or more, Python 3.10 and CUDA drivers. Most people have a Tesla T4 (16 GB); some
have a bigger GPU. `labgpu status` shows which GPU and the monthly limit. It
sleeps when unused. You wake it
with `labgpu up` and run commands on it over SSH (`ssh labgpu ...`). Your user
is probably not technical, so do the technical work yourself and explain
things in plain words.

## 0. Check first: moving from the old account

In October 2026 the lab GPU moved to a new AWS account, with a fresh, empty
machine for each person. Run this before anything else:

```bash
[ -f ~/.labgpu/labgpu.py ] && ! grep -q refresh_config ~/.labgpu/labgpu.py && echo OLD-ACCOUNT
```

If it prints nothing, skip to step 1. If it prints `OLD-ACCOUNT`, the user is
still set up for the old account. Move them over in this order, and **don't
re-run the installer until step 4 below**: after that the old machine can't be
reached.

1. **Sign in to the old account.** Run `~/.labgpu/bin/labgpu status`. If it exits
   with code 3, run `~/.labgpu/bin/labgpu login`. Before the page opens, tell
   the user they'll use their *current* lab GPU password and the code from their
   *existing* SripadaLab-GPU entry in Okta Verify or Duo Mobile. Then run
   `~/.labgpu/bin/labgpu up`.
2. **Back up their files.**
   - Show what's on the machine:
     `ssh labgpu 'du -sh ~/* ~/.[!.]* 2>/dev/null | sort -h'`.
   - Suggest skipping anything that can be re-downloaded or rebuilt: `~/.cache`
     (including Hugging Face models) and virtual environments.
   - Check the user's computer has room, then copy what they approve with
     `rsync -av labgpu:<path> ~/labgpu-old-machine/`.
   - Check the copy finished, then run `~/.labgpu/bin/labgpu down`.
3. **Install and sign in to the new account.** Run the installer (step 1 below),
   then `labgpu login` (step 2 below). This is a first-time sign-in: the
   temporary password is in a *new* "Your Sripada Lab GPU account" email. Tell
   the user the new QR code replaces their old SripadaLab-GPU entry in the app.
4. **Restore.**
   - Run `labgpu up`.
   - Copy the files back with `rsync -av ~/labgpu-old-machine/ labgpu:~/`.
   - Recreate any Python environments they had.
   - Run `labgpu down`.
   - Tell the user, in plain words, what was moved and what was skipped. Keep
     `~/labgpu-old-machine` until they say to delete it.

If the user's temporary password has expired, or anything here fails, ask the
lab admin.

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
- **Model size** depends on the GPU's memory (`labgpu status` shows it):
  - **T4, 16 GB:** up to about 3-4B parameters in 16-bit; 7-8B need 8-bit or
    4-bit quantization.
  - **L4 or A10G, 24 GB:** up to about 8B in 16-bit, 13B in 8-bit, 30B in 4-bit.
  - **L40S, 48 GB:** up to about 20B in 16-bit, 30B in 8-bit, 70B in 4-bit (tight).
  - Bigger than that won't fit. Tell the user the lab admin can give them a
    bigger GPU.
- **Precision and speed:**
  - The T4 is a Turing GPU, the same generation as the lab server. It has no
    fast bfloat16 and no FlashAttention 2, so use float16, or float32 to match
    the lab server's results exactly.
  - L4, A10G and L40S support bfloat16 and FlashAttention 2. Use float32 when
    results must match the lab server exactly.

## 4. Rules

- **No participant data, PHI or other regulated research data** on this machine,
  ever. Public models, synthetic prompts and published datasets only. If a task
  needs participant data, stop and tell the user to ask the lab admin.
- **Cost:** while awake, about $0.53 per hour for a T4, $0.81 for an L4, $1.01
  for an A10G and $1.87 for an L40S. Each person has a monthly limit ($100
  unless the lab admin set another). Check both with `labgpu status`. When the limit is reached, the
  machine can't start until the 1st of next month. Run `labgpu down` when
  you're done instead of waiting for the idle timeout.
- Don't share the machine or its access with anyone else.
