# Handoff to a session running on McKinley's Mac

Everything in this project has been built from a cloud container that cannot
see `/Volumes/The Beast`. A session running on the Mac can, which removes the
screenshot relay entirely.

Paste the block below into that session.

---

## Prompt to paste

> I'm McKinley Griggs, a wedding photographer in St. Louis. I'm building
> **McKinley AI**, a local-first tool that learns which photographs I'd deliver.
> You're picking up work that was built in a cloud session which couldn't reach
> my drives. You can.
>
> **The repo** is at
> `~/Sandbox/McKinley G Photography/R&D Lab/mckinley-ai-repo`, branch
> `claude/mckinley-ai-photography-6d53ak`. Run `git pull` first. The toolkit is
> in `mckinley-ai/audit`, pure Python 3, no dependencies, run as `python3 -m mck`.
> Read `mckinley-ai/README.md` and `mckinley-ai/docs/04-burst-labelling.md`
> before starting.
>
> **What I need you to do, in order:**
>
> 1. Run the labelling survey against my wedding archive:
>    ```
>    cd ~/Sandbox/"McKinley G Photography"/"R&D Lab"/mckinley-ai-repo/mckinley-ai/audit
>    python3 -m mck label --archive "/Volumes/The Beast/~Wedding Catalog" --limit-weddings 10
>    ```
>    It writes `labels/LABELLING-SURVEY.md`. Read it and tell me: how many
>    preference pairs exist, and which wedding it recommends labelling first.
>
> 2. If that finds nothing, the weddings are nested deeper or elsewhere. Try
>    `--depth 6`, then `/Volumes/The Beast/~Wedding Catalog Second` and
>    `/Volumes/The Beast/_Archive`. My backup NAS has the same content if the
>    drive is unplugged.
>
> 3. Once it recommends a wedding, start the labelling UI for it
>    (`--wedding "<name>"`) and tell me the localhost URL. **Check that the
>    images actually render** before I start clicking. The preview extractor
>    has never met a real CR2 or CR3; it scans for embedded JPEG streams and
>    takes the largest. If images are blank, debug it with
>    `python3 -c "from mck.preview import extract_preview, find_jpeg_streams; ..."`
>    against one of my raws and fix `mckinley-ai/audit/mck/preview.py`.
>
> 4. After I've done a labelling sitting, run
>    `python3 -m mck label --raw <same> --summary` and tell me my
>    self-consistency figure.
>
> **Things you need to know:**
>
> - **My folders start with `~`** (`~Wedding Catalog`, `~Branding`). Python's
>   `Path.expanduser()` raises on those; the toolkit already handles it, but
>   don't reintroduce it.
> - **My star scale is Aftershoot's**: 1 rejected, 2 blurry, 3 duplicate,
>   4 keeper, 5 highlight. Stars 1 to 3 are *reason codes*, not a quality
>   ranking. Everything 4 and up gets delivered.
> - **I rarely override Aftershoot**, except in choosing a different duplicate.
>   That override is the only training signal in this project that's actually
>   mine, which is why the whole thing now centres on burst labelling.
> - **Never modify my photographs or sidecars.** Everything here is read-only
>   except `mck cull --write`, which I have not used and you should not use.
> - Run `python3 tests/test_audit.py` after any code change. 51 tests, all
>   passing.
>
> Start with step 1 and tell me what you find.

---

## After the local session has the numbers

Bring back four figures and the decision gets made:

| Number | Where |
| --- | --- |
| Preference pairs available | `LABELLING-SURVEY.md` |
| Bursts with a recorded pick | same |
| Self-consistency on burst winners | the labelling summary |
| Whether previews render on real raws | the UI, by eye |

The third one bounds the whole project. Under about 65% and the right product
is "surface the candidates, let me pick in one click" rather than "pick for me".
