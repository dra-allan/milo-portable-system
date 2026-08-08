---
name: website-flip-pipeline
description: Full website-flip pipeline: prospect to sold site.
version: 1.0.0
author: Milo
tags: [website-flip, prospecting, cold-email, business]
origin: learned
lifecycle: active
pinned: true
created: 2026-08-03
updated: 2026-08-08
---
# Website Flip Pipeline

Turns a business with no website into a finished demo site and a sent cold
email offering to sell it. Does NOT do hosting sales, contracts, or payment
collection — the sell happens by email; the site is a proof-of-work sample.
No external tools required beyond Gmail (Composio) and GitHub.

## When to Use
- "find a business without a website"
- "build a demo site for <business> and email them"
- "next website-flip prospect"
- any cold-email-with-demo-site outreach task

## Prerequisites
- Gmail connected via Composio (this machine: `draallan0@gmail.com`, connected
  account `ca_ykA9HneCKb5y`). Verify with `COMPOSIO_MANAGE_CONNECTIONS` before
  sending.
- GitHub repo created + Pages configured (see deploy step).
- Marketing reference vault: `C:\Users\user\Desktop\dra-brains\11 - Resources\Marketing\`
  (`Marketing.md` is the index; read `allans-takes.md` FIRST before any copy).

## How to Run
Follow the Procedure. End state = demo site live on GitHub Pages + cold email
sent + prospect logged to memory.

## Quick Reference
- Prospect sheet: `C:\Users\user\milo-workspace\website-flip\prospects\`
- Outreach log: `C:\Users\user\milo-workspace\website-flip\outreach\`
- Demo sites: `C:\Users\user\milo-workspace\website-flip\sites\<slug>\`
- Pexels key: `PEXELS_API_KEY` in `C:\Users\user\Desktop\Milo Workspace\website-flip\.env`
- Verify business: Idaho SOS `https://sosbiz.idaho.gov/search` (JS-rendered) or
  `https://www.buildzoom.com` search (reliable, shows license + permit counts)
- Find contact email: business Facebook page `.../posts/` (strips auth wall, page
  ID visible) → About tab for email; Nextdoor for owner name
- Deploy URL pattern: `https://<gh-user>.github.io/<repo>/`
- Cold-email principles: `Marketing\marketing-copywriting.md` + `marketing-email.md`

## Procedure
1. **Hunt.** Google Maps category search in a target city (e.g. "electrician
   Eagle ID"). Pick businesses with NO website in their listing.
2. **Verify + research.** BuildZoom search → confirm licensed, pull permit
   counts, address, phone. Idaho SOS if you need filing/owner details. Note:
   Bizapedia is captcha-blocked and SOSBiz search is JS-rendered — neither
   scrapes; prefer BuildZoom and chamberofcommerce.com listings.
3. **Log prospect.** Append to
   `milo-workspace\website-flip\prospects\<date>-<city>-batch.md` with all
   gathered facts.
4. **Build demo.** In `sites\<slug>\` build a single `index.html` (inline CSS/JS,
   no build step). Load a design skill (design-taste-frontend, brandkit,
   high-end-visual-design) and follow the marketing vault copy rules — NEVER
   write em-dashes in the copy (they read as AI); use commas/parentheses.
   Wire the estimate/contact form to the business's REAL email if found.
   **Design is a HARD RULE:** real Pexels photos per trade (key in
   `website-flip\.env`), ZERO picsum, and each site in a batch must have a
   distinct layout. See HARD RULES below.
5. **Polish (the things that sell it):**
   - hero image must stretch to match the text column (`align-items: stretch`
     on the row + `height:100%` on the img, mobile fallback `aspect-ratio`)
   - subtle hovers: button shine, card tilt, nav underline
   - photo/cert badge inside the image, bottom-left
   - disable scroll restoration: `history.scrollRestoration='manual'` +
     `window.scrollTo(0,0)`
6. **Deploy to GitHub Pages.**
   - push repo `dra-allan/<slug>-demo`
   - Settings → Pages → Source: Deploy from a branch (main) — build_type is
     `legacy`, do NOT pick GitHub Actions
   - after first push, make an empty commit + push again to trigger the build
   - wait ~45s, verify at `https://dra-allan.github.io/<slug>-demo/`
7. **Find contact email.** Open the business's Facebook page and append `/posts/`
   to the URL — the auth wall drops and the page ID is visible in the address
   bar → About → Contact for the email. Cross-check owner name on Nextdoor.
8. **Draft cold email.** Read `allans-takes.md` + copywriting + email md first.
   Principles that work: subject line = headline; sell the feeling not the
   product; name the elephant in the room; story that is also the problem;
   include the LIVE demo link; keep it short. Save to
   `outreach\<date>-<slug>-cold-email.md`.
9. **Send via Composio Gmail.** Subject per step 8, body = draft, recipient
   field MUST be `recipient_email` (not `to`). One send. Log the thread id.
10. **Remember.** `milo remember` the sent email + thread + follow-up date
    (~3-4 days out, one follow-up max).

## HARD RULES (never skip, ALLAN VERDICT 2026-08-08)
These override everything. Violating either = a rejected batch and a wasted
deploy cycle. They apply to ANY model or agent running Milo, in any session.

1. **REAL PHOTOS ONLY. ZERO PICSUM.**
   - Every image on a demo site MUST be a real, on-topic Pexels photo.
   - `picsum.photos` (and any other placeholder service) is BANNED. The
     business is being shown a finished product; a placeholder reads as
     unfinished and kills the sale.
   - Pexels key: read `PEXELS_API_KEY` from
     `C:\Users\user\Desktop\Milo Workspace\website-flip\.env`.
   - Photos must match the TRADE: electrician sites use electrician/panel
     photos, landscapers use lawn/garden photos, painters use roller/wall
     photos, plumbers use pipe/sink photos. Generic or mismatched stock is
     still wrong.
   - Fetch by keyword, pick landscape-orientation shots that fit hero/bento/
     gallery slots, then hard-code the `images.pexels.com` URLs into the HTML.

2. **NO CLONE TEMPLATES. Every site in a batch must be structurally distinct.**
   - Reusing ONE template and just swapping the accent color or brand name is
     a FAIL. Allan called this out on the Eagle ID batch: "the sites you build
     are the same".
   - Vary the layout per site: bento grid, split hero, centered hero + swatch
     bar, asymmetric hero + stacked images, feature-row lists, photo grids,
     marquees, different section ORDER. Change structure, not just color.
   - Shared base CSS (tokens, nav, buttons, reveal) is fine and expected; the
     BODY LAYOUT and hero treatment must differ meaningfully per site.

## Verification GATE (run BEFORE deploy, and again on the live URL)
1. `picsum` count == 0 in the generated HTML.
2. `images.pexels.com` count >= 5 per site.
3. Each site in the batch has a DIFFERENT hero section class and a different
   primary layout section (compare across the batch, not just one site).
4. Inline JS parses: extract `<script>...</script>`, run `node --check`.
   (Use RAW string templates in any generator so backslash-n survives Python.)
Only after all four pass, push to GitHub Pages.

## Pitfalls
- Sending is irreversible — verify the recipient address before the send call.
- GitHub Pages silently no-ops on the first push; the empty-commit push is
  mandatory or the site 404s.
- A `TEMPLATE = """` (non-raw) triple-quoted string with `\n` inside a JS
  mailto body converts to real newlines and throws a SyntaxError in the page;
  always write templates as RAW strings (`r"""`).
- Bizapedia = captcha. SOSBiz = JS-rendered DOM. Don't waste time scraping
  either; use BuildZoom.
- Facebook `/posts/` trick only reveals the page ID, not the email — click
  through to About for the address.
- Em-dashes in body copy = instant "AI wrote this" tell. Also avoid: corporate
  filler openers, "I hope this finds you well".
- Livecrawl/websearch tools time out on some aggregator sites; retry with
  `livecrawl: fallback` before giving up.

## Verification
- Demo loads at `https://dra-allan.github.io/<slug>-demo/` and the form uses
  the business's real email.
- Cold email appears in sent Gmail with a thread id captured.
