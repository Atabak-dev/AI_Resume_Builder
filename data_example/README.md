# Example data files

The pipeline reads its inputs from `data/`, which is gitignored because it holds real
personal data. This folder is the committed, fictional reference for what those files
look like.

## Setup

Copy the three files into your data directory and drop the `_sample` suffix:

```powershell
mkdir data
copy data_example\cv_sample.json            data\cv.json
copy data_example\personal_info_sample.json data\personal_info.json
copy data_example\profile_sample.txt        data\profile.txt
```

```bash
mkdir -p data
cp data_example/cv_sample.json            data/cv.json
cp data_example/personal_info_sample.json data/personal_info.json
cp data_example/profile_sample.txt        data/profile.txt
```

Then replace the contents with your own. Everything in here describes a person who does
not exist.

## What each file is for

| File | Goes to the LLM? | Purpose |
| --- | --- | --- |
| `cv.json` | **yes** | Your full career history, as a superset. Each run selects the subset relevant to the target job. The prompts forbid inventing anything not in this file, so anything missing here cannot appear in the output. |
| `personal_info.json` | **no** | Identity and contact details. Every string in it is scrubbed from the job advert before the first API call, and name / e-mail / phone / location are merged into the finished documents locally. |
| `profile.txt` | **yes** | A few paragraphs in your own voice, passed to the cover-letter prompt as `candidate_profile`. Write it as prose, not bullet points — its job is to give the letter a voice that is recognisably yours. |

## Notes on the shapes

`cv.json` follows the [JSON Resume](https://jsonresume.org/schema/) conventions. The
top-level keys the prompts are written against are `skills`, `work`, `education`,
`projects`, `certificates`, `publications`, `languages`, `interests`, `references` and
`meta`. Sections you leave empty are simply omitted from the generated CV. The file is
handed to the LLM as-is, so extra keys are tolerated — they just become extra context.

`personal_info.json` must keep the `basics` object. `name`, `email`, `phone` and
`location` are read directly by the generator; `location` may be an object (as here) or a
plain string. Everything else, including `profiles`, exists mainly to be scrubbed from the
job advert — the more complete it is, the more thorough the scrub.

Keep `profile.txt` to a handful of paragraphs. It is prepended to every cover-letter
request, so length here costs tokens on every run.

## Optional: profile picture

To show a photo beside your name on the CV, drop an image file anywhere under `data/`
(it never goes to the LLM — only the CV's HTML/PDF) and point `USER_CONFIG.json`'s
`profile_picture` block at it:

```json
"profile_picture": {
  "enabled": true,
  "path": "data/photo.jpg",
  "width_mm": 25,
  "height_mm": 32,
  "corner_radius_mm": 2
}
```

Leave `enabled: false` (the default) to keep the current centered, photo-less header.
