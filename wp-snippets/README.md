# TCS WordPress Snippets

Canonical PHP snippets deployed to `thecanadian.space` via the [Code Snippets plugin](https://wordpress.org/plugins/code-snippets/). **Git is the source of truth; Code Snippets is the deploy vehicle.**

Design rationale for choosing Code Snippets over a mu-plugin is captured in [tcs-scripts#1](https://github.com/The-Canadian-Space/tcs-scripts/issues/1) — short version: this is a WordPress-era-only adapter with a known retirement date, so iteration speed beats long-term stability.

## Files

- [`tcs-header-image.php`](tcs-header-image.php) — reads `_tcs_header_url` post meta (written by the n8n Blog Posting workflow) and injects it as the featured image, OG image, Twitter card image, and RSS thumbnail. Design spec: [tcs-scripts#5](https://github.com/The-Canadian-Space/tcs-scripts/issues/5). Storage: [assets.thecanadian.space](https://github.com/The-Canadian-Space/tcs-docs/blob/main/docs/infrastructure/canonical-assets.md).

## Install a new snippet

1. Open the source file in this directory.
2. Copy the entire file contents.
3. WordPress admin → **Snippets → Add New**.
4. **Remove the opening `<?php` tag** from the top of what you paste — Code Snippets evals the body directly and rejects a raw `<?php`.
5. Name the snippet (e.g. `TCS Header Image v1.0.0`).
6. Description: link to the file path in this repo + the version.
7. Scope: **Run everywhere**.
8. If your Code Snippets version has a **"Test"** or **"Save and Preview"** button, use it first — it catches PHP fatals before the snippet goes live.
9. Once preview is clean → **Save and Activate**.

## Update an existing snippet

Whichever way you edit, the other side must follow — otherwise the two go out of sync and the next redeploy regresses prod.

**If you edited in WP admin:**

1. Copy the updated PHP out of the Code Snippets editor.
2. Paste into the corresponding `.php` file in this repo.
3. Add back the `<?php` opening tag at the top (git file has it; WP paste doesn't).
4. Bump the `Version:` line in the docblock.
5. PR + merge.

**If you edited in git first:**

1. Merge the PR.
2. Copy the updated file's contents (skip the `<?php`).
3. WP admin → Snippets → select the existing snippet → Edit.
4. Paste. Save + Activate.

## Versioning

Each file's header docblock includes a `Version:` line. Bump on every change. Follow [SemVer](https://semver.org/) loosely:

- **patch** — bugfix, no behavior change (e.g. tightening an escape function)
- **minor** — new hook added or new opt-in behavior
- **major** — breaking change (e.g. renaming the meta key it reads)

The version also lands in the snippet's WP admin name so we can see at a glance which version is deployed.

## FIFU retirement (once every new post ships with `_tcs_header_url`)

The `tcs-header-image.php` snippet is designed to fall through cleanly when a post has no `_tcs_header_url` meta — the featured image renders via WP native / FIFU / whatever else exists. That lets us keep FIFU installed during the transition period and phase it out only when every post we care about has been through the new pipeline.

Rough retirement plan:

1. Ship the snippet + start writing `_tcs_header_url` in Blog Posting ([tcs-workflows#65](https://github.com/The-Canadian-Space/tcs-workflows/issues/65)).
2. Wait 5–7 new posts, verify each one renders correctly via `assets.thecanadian.space` on the front page + singular view + RSS.
3. Deactivate FIFU from WP admin.
4. Verify older (FIFU-era) posts still render — the WP-native featured image should take over.
5. Uninstall FIFU once we're confident.

Older posts stay unaffected unless we choose to backfill (out of scope for V1).

## Related

- Umbrella: [tcs-scripts#1 — Blog Header Images V1](https://github.com/The-Canadian-Space/tcs-scripts/issues/1)
- Milestone: [Blog Header Images V1](https://github.com/The-Canadian-Space/tcs-scripts/milestone/1)
- Storage principle: [tcs-docs → Canonical assets](https://github.com/The-Canadian-Space/tcs-docs/blob/main/docs/infrastructure/canonical-assets.md)
- Brand reference: [tcs-docs → Brand](https://github.com/The-Canadian-Space/tcs-docs/blob/main/docs/reference/brand.md)
