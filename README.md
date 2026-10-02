# UNT Classroom Finder

Run `start_classroom_finder.bat` from the repository root, then open http://127.0.0.1:5175/room-finder/.

The interface shares VSB's neutral light/dark surfaces, green accents, cycling system theme, pastel occupancy colors and responsive controls. Campus, building, room, course, date/time and free-window filters work together. Course filtering does not hide other classes when computing room occupancy. Unknown schedules remain unknown; no scheduled class is not a reservation or guaranteed vacancy.

Run `scrape_fall_2026.bat` to resume missing/failed courses or choose refresh. It uses the exact VSB parser, linked meetings, retry handling, atomic checkpoints and pause/resume/stop controls, with a fixed Fall term and isolated output. Keep the terminal open; stop safely with q or Ctrl+C. Reload data after saved progress. The Fall catalog has 7,159 courses, so full collection is a long-running job.

Hosted scraping uses the shared VSB engine with dedicated Fall checkpoint and publication keys. It remains disabled until Cloudflare R2 credentials, workflow variables and hosted-browser smoke validation are configured. Complete-publication validation leaves the last release untouched on failures or incomplete refreshes. Cloudflare preparation alone does not protect the local/static dataset from copying.

A live isolated Fall smoke test retrieved MATH 1720 with 19 enrollment options. That verified result was merged into the partial local Fall snapshot. Full Fall coverage still requires resume/refresh; no synthetic room data was added.

This is the standalone repository. It no longer needs a VSB checkout to run or scrape. VSB removal is deferred until its PR #3 is resolved and the owner confirms which branch to remove.

## Daily and live views

Today’s classes shows all dated meetings for the chosen day and supports Time, Building, or Course organization; filters can show all day, happening now, or upcoming. A meeting opens the classroom’s full-day schedule and 24-hour timeline. Live mode follows the current Central time every 30 seconds and checks the saved dataset every five minutes while visible. Editing date/time pauses live mode. This reflects published schedules, not physical occupancy sensors.

Public wording stays focused on classroom schedules and campus class activity. The room availability filters are available without advertising the product as a study-room finder.

