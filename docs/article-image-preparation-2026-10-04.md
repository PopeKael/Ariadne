# Article image preparation — 4 October 2026

## Objective

Discover cards should show the publisher's meaningful lead image through a local cache. A failed feed image must fall through to page extraction, and failures must remain retryable.

## Architecture now

Article Cache on Hera owns publisher HTML fetching and preparation. It extracts text and tries RSS, OG variants, Twitter variants, JSON-LD article images and primary images, image_src, then scoped article/hero images. It validates image MIME, dimensions, structure and decoded pixels with Pillow before atomically writing the local image. Attempt details, final article URL, chosen method, cache location and retry time persist alongside the article.

Discovery requests that preparation before Signal intake. Signal transfers the prepared local image into its existing image-serving cache without refetching the publisher. External intake retains the safety fallback; a broken feed hint now falls through to page lookup.

The separate News Backend that supplies Home also requests the same preparation service. It retains its existing Markdown store, curation and interaction state. Its card records now carry authoritative local image URLs and preparation diagnostics. Existing Signal-to-Home synchronization handles legacy cards only and cannot overwrite prepared records.

## Live deployment

- Discovery, News Backend and Signal build: `20261004-article-preparation-1`; Article Cache MIME correction: `20261004-article-preparation-2`.
- All four service health endpoints confirmed this build after deployment.
- Stage: `/volume1/docker/ariadne-article-preparation-20261004`.
- Original containers retained as `<original-name>-before-20261004-article-preparation-1`.
- SQLite backups and original container inspection records retained in the stage's timestamped `before-*` directory. Keep inspection records private; they contain deployment environment settings.
- Existing build contexts were synchronized with the staged code, preserving source configuration and data. Original source backup: `source-before-preparation.tar.gz` in the stage.
- Discovery's existing Compose configuration now points at Article Cache. Signal's Compose build identifier and image tag were updated so a future normal rebuild retains this fix.
- Checkpoint scope and validation are recorded in [checkpoint-2026-10-04-article-images.md](checkpoint-2026-10-04-article-images.md). Runtime images, databases, deployment inspection records and terminal transcripts are excluded from Git.

## Verification

- 21 Discovery/preparation tests passed on the final source, including real HTTP publisher/cache transfer, bad-feed fallback, one HTML fetch, image validation, text preservation, retry throttling, MIME independence and healthy cache reuse.
- 7 News Backend tests, 8 news snapshot/cache tests and 5 Home snapshot integration tests passed.
- 10 focused Signal image/HTTP tests passed with semantic enrichment isolated from the image tests.
- The broad Signal test run was not completely green. `test_intake_only_refresh_preserves_healthy_cached_briefing` produces the same `KeyError: mode` on both original and changed code. The broad HTTP test hit its two-second timeout during external probes; its focused isolated run passed.
- Three previously blank live Al Jazeera articles were prepared on Hera and their locally served image bytes decoded successfully: ordinary news (1920×1440), sports (1920×1440), and NewsFeed video (1920×1080).

Known article IDs:

```text
article-b20912b2756931d3e387af6647fc  Brazil news report
article-da9b367cf3d8f9c12d572a19dda1  Fernandes/Ronaldo sports report
article-081666fa998bfd84aa7f08f3c710  Mamdani/Irish PM NewsFeed video
```

## Final acceptance

The first finishing run attached all three known cards but stopped before broader backfill: Hera's Python MIME registry served WebP as `application/octet-stream`. A deterministic MIME map now covers every supported image format, with a passing registry-independent regression test. `repair_article_cache_mime.py` deployed only Article Cache as build `20261004-article-preparation-2`, then resumed the finishing script. The terminal confirmed `BACKFILL_COMPLETE` and exit code 0 at 09:20 on 2026-10-04. WebP now serves as `image/webp`.

The separate `Run-ArticleImageRepair.ps1` window uses a transcript at `runtime/article-image-repair-terminal.log`. Do not ask the user to paste commands into an unrelated idle terminal. Read terminal output before reporting progress. Codex's terminal-opening tool did not attach the execution session; it opened an idle shell.

Windows PowerShell package was upgraded through WinGet from 7.6.5.0 to 7.6.6.0. Both the launched executable and package inventory confirm 7.6.6 after closing the identified old idle packaged-shell processes. The Codex-bundled runtime remains a separate copy. The successful password window was launched in Warren's Windows session using the packaged shell and the exact password SSH overrides.

The finishing script attached the three known records and repaired missing images in Hera's current briefing, preserving healthy caches. Home also retains older locally ranked cards; 29 missing images on those retained cards needed preparation. The new background `sync_prepared_images` worker uses the same shared preparation owner in bounded batches, preserving order, feedback and card text. It excludes healthy image references, respects retry times and keeps full article bodies out of the card snapshot. `prepare_retained_card_images.py` warmed only missing images on actual Home cards. All 100 resulting image URLs returned bytes that decoded successfully; proof: `runtime/home-image-byte-verification.json`.

```powershell
ssh -t -o BatchMode=no -o PubkeyAuthentication=no -o PreferredAuthentications=password Wazza@192.168.1.200 "sudo sh /volume1/docker/ariadne-article-preparation-20261004/finish_article_preparation.sh"
```

The local core was restarted to load the retained-card worker. Two retained-card regression tests, eight snapshot/cache tests and five Home integration tests passed after the final change. Discovery's first normal refresh completed at 09:37:09: 202 preparations ready, 38 unavailable; all 240 candidates handed to Signal successfully in 12 batches without failed batches. Signal semantic health remained healthy. Unavailable preparation records keep diagnostics and bounded retry metadata; they are not fabricated successes.

Final visible acceptance at 09:39 on 2026-10-04: local Home snapshot contains 100 cards with images and no missing image references; the rendered DOM contains 100 images and zero placeholder elements. The news, sports and video images loaded with nonzero decoded dimensions. Screenshot: `runtime/article-preparation-home-final.jpg`, showing the repaired sports card and another previously blank retained card. No further restart or backfill is pending. Warren confirmed the visible result and requested the GitHub checkpoint.

## Access boundary

Follow [hera-ssh-access.md](hera-ssh-access.md). Warren's interactive SSH requires all three password-login overrides above. Codex's successful key-based SSH does not provide sudo and cannot reuse the terminal's sudo timestamp. Passwords stay in the terminal.

## Rollback

Stop and rename a replacement container, rename its retained original back to the original name, then start it. Perform this with authenticated sudo. Preserve the databases and images; their schema changes are additive. Original source contexts and SQLite snapshots are retained for review or recovery.
